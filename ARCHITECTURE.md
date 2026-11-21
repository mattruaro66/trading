# ATLAS B3 — Arquitetura

> Plataforma de **pesquisa, monitoramento, simulação e apoio analítico** para WIN e WDO.
> **Não executa ordens.** Não existe nenhum módulo de roteamento, envio de ordens ou copy trading — e nenhum deve ser adicionado.

## 1. Princípios de projeto

| Princípio | Como é aplicado no código |
|---|---|
| Timeframe único (5 min) | `atlas.core.timeframe.TIMEFRAME = 5min` é constante; agregadores e validadores rejeitam qualquer outro período. |
| Integridade temporal | Todos os motores analíticos são **incrementais**: recebem um candle fechado por vez (`on_bar_closed`) e não possuem acesso a candles futuros. Backtest, replay e live usam a mesma classe. |
| Rastreabilidade | Cada alerta carrega `strategy_id`, `strategy_version`, `engine_version`, parâmetros, snapshot dos candles usados e um `content_hash` SHA-256. |
| Reprodutibilidade | Sem aleatoriedade não-semeada (bootstrap usa `seed` explícita); dados persistidos com hash; parâmetros versionados. |
| Separação de responsabilidades | Explicações são **renderizadas a partir de números calculados**; nenhum texto gera números. LLM é opcional e fora do MVP. |
| Sem dados inventados | Campos indisponíveis ficam `None`. Gaps nunca são preenchidos. Probabilidade sem amostra mínima → mensagem de evidência insuficiente. |

## 2. Visão geral dos módulos

```
               ┌────────────────────────── Fontes ───────────────────────────┐
               │ CSV/Parquet (histórico)   ReplayProvider   CedroProvider*   │
               └──────────────┬──────────────────────┬────────────────────────┘
                              │ (OHLCV 5m)           │ MarketEvent (trades)
                              ▼                      ▼
                 ┌────────────────────┐    ┌───────────────────────┐
                 │ Market Data Engine │    │ Live: CandleEngine +   │
                 │ import/valid/store │    │ DataQualityMonitor     │
                 └─────────┬──────────┘    └──────────┬────────────┘
                           │ Bar (fechado)            │ CandleClosed / DataGap / Degraded
                           └────────────┬─────────────┘
                                        ▼
                       ┌─────────────────────────────────┐
                       │  AnalysisPipeline (único)        │  ← usado por backtest, replay e live
                       │  Price Action Engine (barras,    │
                       │  regime, H1/H2/L1/L2)            │
                       │  Strategy Engine (Second Entry)  │
                       └───────┬─────────────┬───────────┘
                               │ Signals     │ SetupEvents
                 ┌─────────────▼───┐   ┌─────▼──────────────┐
                 │ Backtest Engine │   │ Alert Engine        │
                 │ (execução simul.│   │ (estados, dedup,    │
                 │  custos, ticks) │   │  cooldown, imutável)│
                 └───────┬─────────┘   └─────┬──────────────┘
                         ▼                   ▼
                 ┌────────────────┐  ┌───────────────────┐
                 │ Probability /  │  │ Explanation Engine │
                 │ Statistics     │  │ (pt-BR determinís.)│
                 └───────┬────────┘  └─────┬─────────────┘
                         └──────┬───────────┘
                                ▼
                    FastAPI (REST + WebSocket)  →  Dashboard React
```
`*` CedroProvider: pendente de documentação oficial e credenciais (ver `PROVIDER_INTEGRATION.md`).

### Módulos e responsabilidades

| # | Módulo | Pacote | Responsabilidade | Status Fase 1 |
|---|---|---|---|---|
| 1 | Market Data Engine | `atlas.market_data` | Importar CSV/Parquet, normalizar, validar OHLCV e grade de 5 min, armazenar (Parquet + SQL) | Implementado |
| 2 | Contract Management | `atlas.contracts` | Especificações versionadas (tick, valor do ponto), calendário de feriados, sessões, códigos de vencimento, resolução explícita de contrato, rolagem | Implementado (dados a validar com a B3) |
| 3 | Price Action Engine | `atlas.price_action` | Métricas de barra, classificação de barras, regime (tendência/range), contagem H1/H2/L1/L2 | Implementado (subconjunto do MVP) |
| 4 | Trading Knowledge Engine | `atlas.knowledge` | Catálogo de conceitos com separação conceito/interpretação/regra/resultado; registro de referências | Catálogo estático; RAG na Fase 2 |
| 5 | Strategy Engine | `atlas.strategy` | Estratégias declarativas e versionadas; Estratégia A (Second Entry) | Implementado |
| 6 | Probability Engine | `atlas.probability` | Métricas, intervalos de confiança (Wilson, bootstrap), split temporal, walk-forward, Brier | Implementado (básico) |
| 7 | Backtesting Engine | `atlas.backtest` | Execução simulada com ticks, custos, slippage, política conservadora intrabar | Implementado |
| 8 | Macro Intelligence | `atlas.macro` | Modelo de eventos macro com disponibilidade temporal | Somente modelo de dados (Fase 4) |
| 9 | Alert Engine | `atlas.alerts` | Estados, deduplicação, cooldown, registro imutável com hash, bloqueio por dados ruins | Implementado |
| 10 | Explanation Engine | `atlas.explanation` | Explicações A–G em português a partir de dados calculados | Implementado (determinístico) |
| 11 | Historical Replay | `atlas.replay` | Sessões de replay candle a candle no servidor, sem expor candles futuros | Implementado |
| 12 | Dashboard | `frontend/` | React + TS + Vite + Tailwind + Lightweight Charts | Implementado (MVP) |
| 13 | Audit & Reporting | `atlas.reporting`, `atlas.alerts.store` | Hash de conteúdo, logs estruturados, export JSON/CSV | Implementado (básico) |
| L | Live Market Data | `atlas.live` | Interface `MarketDataProvider`, eventos canônicos, CandleEngine, qualidade de dados, Replay/CSV providers | Etapas A, B, D(parcial), E implementadas; C pendente |

## 3. Estrutura de diretórios

```
trading/
├── README.md, ARCHITECTURE.md, ROADMAP.md, DATA_SOURCES.md, STRATEGIES.md, TEST_PLAN.md
├── LIVE_DATA_ARCHITECTURE.md, PROVIDER_INTEGRATION.md, DATA_QUALITY.md, LIVE_DATA_TEST_PLAN.md
├── docker-compose.yml, .env.example
├── backend/
│   ├── pyproject.toml, Dockerfile, alembic.ini, alembic/
│   ├── data/reference/            # especificações, feriados e sessões (versionados, a validar)
│   ├── atlas/
│   │   ├── core/                  # tipos, timeframe, tempo, logging, erros
│   │   ├── contracts/             # specs, calendário, sessões, resolver, rolagem
│   │   ├── market_data/           # importadores, validação, armazenamento parquet
│   │   ├── live/                  # provider ABC, eventos, candle engine, qualidade, providers
│   │   ├── price_action/          # barras, regime, contagem de pernas H/L
│   │   ├── strategy/              # especificação declarativa, Second Entry
│   │   ├── pipeline.py            # AnalysisPipeline — caminho único para backtest/replay/live
│   │   ├── backtest/              # custos, execução, engine, relatório
│   │   ├── probability/           # estatísticas, IC, walk-forward, calibração
│   │   ├── alerts/                # estado, dedup, cooldown, store imutável
│   │   ├── explanation/           # renderização pt-BR
│   │   ├── replay/                # sessões de replay
│   │   ├── knowledge/             # catálogo de conceitos Brooks + referências
│   │   ├── macro/                 # modelo de eventos (fase 4)
│   │   ├── persistence/           # SQLAlchemy models + repositórios
│   │   ├── api/                   # FastAPI (REST + WS)
│   │   └── cli.py                 # CLI (import, backtest, gerar dados SINTÉTICOS)
│   └── tests/
└── frontend/                      # React + Vite + Tailwind + lightweight-charts
```

## 4. Modelo de dados (canônico)

### 4.1 Bar (candle de 5 min) — `atlas.core.types.Bar`
| Campo | Tipo | Nota |
|---|---|---|
| `symbol` | `"WIN" \| "WDO"` | raiz do instrumento |
| `contract` | `str \| None` | ex.: `WINZ26`; `None` só para série contínua declarada |
| `series_kind` | `contract \| continuous` | séries contínuas nunca são confundidas com contratos negociáveis |
| `ts_open`, `ts_close` | `datetime` tz-aware (America/Sao_Paulo) | `ts_close = ts_open + 5min` |
| `open, high, low, close` | `float` (múltiplos do tick) | validados |
| `volume` | `float \| None` | |
| `trades` | `int \| None` | número de negócios quando disponível |
| `status` | `forming \| closed \| invalid` | só `closed` alimenta o pipeline |
| `source` | `str` | proveniência (arquivo, provedor) |
| `quality` | `ok \| suspect \| invalid` + `quality_flags` | |
| `synthetic` | `bool` | **true** para dados sintéticos (testes/demonstração) |

### 4.2 MarketEvent (live) — `atlas.live.models.MarketEvent`
`provider, symbol, contract, event_type (trade|quote|status), exchange_timestamp, received_timestamp, price, quantity, trade_id, sequence_number, data_quality_status, raw`. Campos ausentes = `None`.

### 4.3 Persistência (SQL)
Tabelas (ver `atlas/persistence/models.py` e migração Alembic `0001`):
`instruments`, `contract_specs` (versionadas), `contracts`, `data_imports`, `candles`, `providers`, `connection_events`, `strategy_versions`, `alerts` (imutável), `alert_events` (append-only), `backtest_runs`.
Candles históricos também ficam em Parquet (`data/store/{symbol}/{contract}.parquet`) para análise vetorizada.

## 5. Contratos entre módulos

```python
# Price Action (incremental, sem look-ahead)
class PriceActionEngine:
    def on_bar_closed(self, bar: Bar) -> PriceActionSnapshot  # métricas, regime, contagem de pernas, setups

# Estratégia
class Strategy(Protocol):
    spec: StrategySpec          # declarativa + versionada
    def on_snapshot(self, snap: PriceActionSnapshot) -> list[Signal]

# Pipeline único
class AnalysisPipeline:
    def on_bar_closed(self, bar: Bar) -> PipelineOutput   # snapshot + signals + setup events

# Backtest
def run_backtest(bars: Sequence[Bar], strategy_spec, cost_model, ...) -> BacktestResult

# Live
class MarketDataProvider(ABC):  connect/disconnect/subscribe/unsubscribe/get_status/get_historical_data
                                 + callbacks on_trade/on_quote/on_error
class CandleEngine:  ingest(MarketEvent) -> list[CandleEngineEvent]; advance_clock(now) -> list[...]
```
Os detalhes tipados estão nos docstrings dos módulos; as fronteiras são testadas em `backend/tests`.

## 6. Dependências

Backend: Python ≥ 3.12 (testado em 3.13), FastAPI, Pydantic v2, pandas, NumPy, SciPy, scikit-learn (Brier/calibração), SQLAlchemy 2, Alembic, PyArrow, openpyxl, pytest.
Frontend: React 18, TypeScript, Vite, Tailwind CSS, lightweight-charts.
Infra: Docker Compose (PostgreSQL 16, backend, frontend). Redis **não** é usado no MVP (nada exige cache distribuído ainda).

## 7. Decisões importantes

1. **Uma implementação das regras.** `AnalysisPipeline` é a única porta de entrada das regras de Price Action e estratégia. O backtester apenas alimenta candles fechados e simula a execução dos sinais; o replay e o live fazem o mesmo.
2. **Sinal ≠ alerta.** Reconhecer um H2 gera um `SetupEvent`; só vira alerta “Configuração confirmada” se contexto, qualidade da barra de sinal, horário e qualidade dos dados forem aprovados pela estratégia.
3. **Política intrabar conservadora.** Se stop e alvo são tocados no mesmo candle sem dado intrabar, assume-se o stop (documentado em `STRATEGIES.md`).
4. **SQLite em desenvolvimento/testes, PostgreSQL em Docker.** Mesmo código SQLAlchemy; `ATLAS_DATABASE_URL` decide.
5. **Especificações e calendário como dados versionados** (`backend/data/reference/*.json`) com `validated: false` até conferência com a B3 — o sistema exibe esse status.
