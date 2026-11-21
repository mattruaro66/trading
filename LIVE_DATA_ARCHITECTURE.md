# ATLAS Live Market Data — Arquitetura

Módulo `backend/atlas/live/`. Objetivo: receber negócios de WIN/WDO de um provedor autorizado e construir candles de **5 minutos** em tempo real, alimentando o mesmo `AnalysisPipeline` usado no backtest e no replay. **Não executa ordens.**

## 1. Componentes

```
 MarketDataProvider (ABC)                    atlas/live/provider.py
 ├── ReplayProvider   (simulado, sempre rotulado)   atlas/live/providers.py
 ├── CSVProvider      (somente histórico, sem "conexão")
 └── CedroProvider    (PENDENTE — falha explicitamente; nenhum endpoint presumido)
          │ on_trade / on_quote / on_error  (MarketEvent canônico)
          ▼
 LiveMonitor                                  atlas/live/monitor.py
   ├── CandleEngine (5 min, só negócios)      atlas/live/candle_engine.py
   ├── heartbeat, latência (p50/p95), reconexão com backoff + jitter semeado
   ├── recuperação → CandleEngine.rebuild()
   ├── AnalysisPipeline (Price Action + Estratégia + simulador)  atlas/pipeline.py
   ├── AlertEngine (suspensão por qualidade de dados)
   └── Repository (candles fechados, eventos de conexão, alertas)
          │ publish(dict)
          ▼
 WsHub → WS /ws/market → Dashboard (aba "Dados ao vivo")
```

## 2. Interface do provedor (`MarketDataProvider`)

| Método | Contrato |
|---|---|
| `connect()` | Autentica e conecta. `AuthenticationError` para credenciais rejeitadas. |
| `disconnect()` | Encerra a conexão; idempotente. |
| `subscribe(symbol)` | `symbol` = **código completo do contrato** (ex.: `WINZ26`). |
| `unsubscribe(symbol)` | — |
| `get_status()` | `ProviderStatus` (estado, simulado?, assinaturas, último evento, latência, mensagem). |
| `get_historical_data(symbol, start, end)` | Candles históricos, se suportado. |
| `recover_events(symbol, start, end)` | Negócios de um intervalo, se `supports_recovery`. |
| `on_trade/on_quote/on_error(handler)` | Registro de callbacks (sync ou async). |

Trocar de fornecedor = escrever um novo adaptador; nenhum outro módulo muda.

## 3. Modelo canônico (`MarketEvent`)
`provider, symbol, contract, event_type (trade|quote|status), exchange_timestamp, received_timestamp, price, quantity, trade_id, sequence_number, bid, ask, data_quality_status, synthetic, raw`.
Timestamps obrigatoriamente com fuso. Campos ausentes = `None`. `raw` só deve ser preenchido quando a licença do fornecedor permitir armazenar o evento original.

## 4. Eventos tipados publicados
`CandleUpdated` (candle em formação), `CandleClosed`, `CandleInvalidated`, `DataGapDetected`, `ConnectionDegraded`, `ConnectionRecovered` (`atlas/live/models.py`).

Somente `CandleClosed` alimenta o `AnalysisPipeline`. O candle em formação é exibido no gráfico, mas **nunca** é usado como candle confirmado.

## 5. Fluxo de falha
1. Erro do provedor ou heartbeat vencido → `ConnectionDegraded`; candles abertos são marcados `connection_degraded`.
2. `AlertEngine.set_data_status(False)` → alertas suspensos.
3. Reconexão com backoff exponencial (1 s, 2 s, 4 s… até 60 s, jitter ±20%, semente fixa, `max_attempts`).
4. Se o provedor suporta recuperação: solicita negócios desde a **abertura do primeiro candle afetado** até o ponto de retomada.
5. `CandleEngine.rebuild()` reconstrói integralmente os candles afetados (flag `rebuilt`).
6. Sem recuperação: candles afetados permanecem **inválidos**; o pipeline vê a lacuna e reinicia (novo aquecimento do regime).
7. Alertas são retomados apenas com conexão normal + recuperação concluída + **um candle limpo** fechado.

Autenticação rejeitada durante a reconexão → estado `failed` sem novas tentativas (não insistir com credenciais inválidas).

## 6. Mesma lógica em três modos
| Modo | Fonte de candles | Regras |
|---|---|---|
| Backtest | candles armazenados | `AnalysisPipeline` |
| Replay histórico | candles armazenados, revelados um a um | `AnalysisPipeline` |
| Live / replay de negócios | `CandleEngine` | `AnalysisPipeline` |

Testes garantem equivalência: replay × backtest produzem as mesmas operações (`tests/test_alerts_replay.py::test_replay_matches_backtest`); candles reconstruídos após queda são idênticos aos do fluxo completo (`tests/test_live_monitor.py`).

## 7. API
| Rota | Descrição |
|---|---|
| `GET /api/market/status` | Estado do monitor (provedor, latência, qualidade, motivo de bloqueio de alertas) + eventos de conexão |
| `GET /api/market/contracts?symbol=&on=` | Especificação, vencimentos listados e sugestão auditável de contrato |
| `GET /api/market/candles?symbol=&contract=` | Candles armazenados |
| `GET /api/market/providers` | Provedores e status (Cedro: `not_configured`) |
| `POST /api/live/start` | Inicia `ReplayProvider` com negócios **sintéticos** (contrato `*_SINTETICO`); `provider=cedro` → 501 |
| `POST /api/live/start-file` | Replay de um CSV de negócios do usuário |
| `POST /api/live/stop`, `GET /api/live/events` | — |
| `WS /ws/market` | Difusão de todos os eventos acima |

Credenciais ficam em variáveis de ambiente do backend (`ATLAS_CEDRO_*`) e nunca são retornadas pela API.
