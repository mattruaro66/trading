# ATLAS B3 — Plano de Testes e Critérios de Aceitação

## 1. Como executar
```bash
cd backend
pip install -e ".[dev]"
pytest -q                                   # suíte completa (SQLite em arquivos temporários)
ATLAS_TEST_POSTGRES_URL=postgresql+psycopg2://user:senha@localhost:5432/atlas_test pytest -q tests/test_postgres_optional.py
cd ../frontend && npm install && npm run build   # checagem de tipos + build
```
Todos os dados de teste são **sintéticos** e marcados como tal.

## 2. Cobertura por área

| Área | Arquivo | O que garante |
|---|---|---|
| Especificações / ticks / R$ | `test_contracts.py` | tick e valor por ponto de WIN/WDO; P&L em R$; arredondamento ao tick; códigos e vencimentos; calendário; só 5 min |
| Importação / validação | `test_import_validation.py` | formatos (ATLAS, MT5, PT-BR, Parquet); rejeições; duplicidades; lacunas não preenchidas; rótulo de timestamp; 1 min → 5 min; contrato obrigatório |
| Indicadores / padrões | `test_price_action.py` | métricas; ATR sem a barra atual; regime; sequência H1/H2 e espelho L1/L2; propriedade “candidata a sinal ⇒ contagem no candle seguinte”; reinícios |
| Estratégia / execução / custos | `test_strategy_execution.py` | níveis de entrada/stop/alvo; filtros; política conservadora intrabar; gaps; saída por tempo, sessão e lacuna; custos e slippage em WDO; uma posição por vez |
| Look-ahead / backtest / estatística | `test_lookahead_backtest.py` | invariância a truncamento; mutação de candles futuros não altera o passado; reprodutibilidade (`run_id`); contabilidade; Wilson/bootstrap/drawdown; mensagem de evidência insuficiente; walk-forward |
| Alertas / explicações / replay | `test_alerts_replay.py` | ciclo de estados; imutabilidade e cadeia de hashes; supressão por dados; dedup/cooldown; seções A–G; replay não revela candles futuros; replay ≡ backtest |
| Persistência / API | `test_persistence_api.py` | roundtrip com fuso; alertas append-only; fluxo ponta a ponta via API; validação de entrada; ausência de rotas de ordens |
| Live | `test_live_*.py` | ver `LIVE_DATA_TEST_PLAN.md` |
| PostgreSQL | `test_postgres_optional.py` | roundtrip em PostgreSQL real (opcional) |

## 3. Resultados da última execução
- Backend: **111 aprovados, 1 ignorado** (o teste opcional de PostgreSQL, que foi executado separadamente contra um PostgreSQL 16 local e **passou**).
- Migração Alembic `0001` aplicada com sucesso em SQLite e PostgreSQL 16.
- Frontend: `tsc -b` e `vite build` sem erros; navegação verificada com Playwright (Painel, Laboratório/backtest, Replay com alerta e explicação, Dados ao vivo, Metodologia) sem erros de console.
- **Não validado:** `docker compose up` — no ambiente de desenvolvimento o Docker Hub respondeu 429 (limite de requisições) e o build não alcançou o PyPI. Validar localmente.

## 4. Critérios de aceitação do MVP (§20)

| # | Critério | Situação |
|---|---|---|
| 1 | Iniciar localmente por comandos documentados | ✅ README (uvicorn + vite). Docker Compose escrito, **não validado** neste ambiente |
| 2 | Importar dados históricos válidos de WIN/WDO | ✅ funcionalidade testada; **depende de o usuário fornecer arquivo real** |
| 3 | Gráficos de 5 min | ✅ |
| 4 | Padrões com regras testáveis | ✅ |
| 5 | Contextos básicos | ✅ (tendência alta/baixa, range, range estreito) |
| 6 | Backtests reproduzíveis | ✅ (`run_id` determinístico) |
| 7 | Custos financeiros corretos | ✅ (testado com valores do prompt; tarifas reais a configurar) |
| 8 | Estatísticas verificáveis | ✅ |
| 9 | Replay sem informação futura | ✅ |
| 10 | Alertas simulados | ✅ |
| 11 | Explicar cada alerta | ✅ (determinístico) |
| 12 | Testes automatizados | ✅ |
| 13 | Limitações documentadas | ✅ |
| 14 | Não apresentar dados fictícios como reais | ✅ (sufixo `_SINTETICO`, flag `synthetic`, faixas na UI, avisos nos relatórios e explicações) |
| 15 | Não executar ordens | ✅ (sem módulo de ordens; teste verifica ausência de rotas) |

Pendências que impedem declarar a Fase 1 “validada” (não apenas “implementada”): dados reais licenciados de WIN/WDO e conferência das especificações/feriados/horários com a B3.
