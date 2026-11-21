# ATLAS B3 — Roadmap

Regra geral: **uma fase só avança quando seus critérios de aceitação estão atendidos com testes executados** — nunca porque a interface “parece pronta”.

| Fase | Escopo | Critérios de saída | Status |
|---|---|---|---|
| **1 — MVP histórico** | Importação CSV/Parquet, validação OHLCV, contratos/ticks, gráficos 5 min, barras, regime, H1/H2/L1/L2, Estratégia A, backtest com custos, estatísticas + OOS, replay, alertas simulados, explicações determinísticas | Os 15 critérios de `TEST_PLAN.md §4` | Em andamento — ver checklist abaixo |
| L — Live Data (A,B,D,E,F) | Interface de provedor, eventos canônicos, CandleEngine, Replay/CSV providers, qualidade de dados, supervisão de conexão, WS | `LIVE_DATA_TEST_PLAN.md` | A, B, E implementadas; D parcial; F parcial |
| L — Live Data (C) | CedroProvider real | Documentação oficial + contrato + credenciais + **teste real de conexão** | **Bloqueada** (ver `PROVIDER_INTEGRATION.md`) |
| 2 — Biblioteca Brooks + RAG | Spike/canal, canais largos/estreitos, wedges, double top/bottom, MTR, falhas de rompimento; importação de material com licença registrada; consulta com citação verificável | Cada padrão com testes de reconhecimento e caso negativo; RAG nunca retorna trecho sem referência | Não iniciada (catálogo estático pronto) |
| 3 — Probabilidades calibradas | Regressão logística, calibração (Platt/isotônica), curvas de calibração, controle de múltiplos testes, degradação | Brier fora da amostra melhor que a taxa-base; relatório de sensibilidade | Parcial (Wilson, bootstrap, Brier vs taxa-base, Bonferroni) |
| 4 — Macro | Calendário (BCB/SGS, IBGE, Tesouro, Fed/BLS/BEA), eventos com `available_at`, surpresa | Nenhum evento usado antes de `available_at` (teste) | Modelo de dados apenas |
| 5 — Tempo real | Feed autorizado, monitoramento contínuo WIN/WDO | Critérios do conector §13 com dados reais | Bloqueada pela fase L-C |
| 6 — IA explicativa | LLM só reescreve explicações sobre números já calculados; verificação de que nenhum número novo aparece | Teste automático de “números no texto ⊆ números no contexto” | Não iniciada |
| 7 — Paper trading/monitoramento | Registro prospectivo, comparação com backtest, degradação | Relatório mensal automatizado | Não iniciada |

## Checklist da Fase 1 (atualizado a cada etapa)

- [x] Documentação de arquitetura
- [x] Especificações de contrato versionadas + cálculo financeiro
- [x] Importação CSV/Parquet + validação
- [x] Métricas e classificação de barras
- [x] Regime (tendência/range)
- [x] H1/H2/L1/L2
- [x] Estratégia A
- [x] Backtest com custos e política conservadora
- [x] Estatísticas, IC, split temporal, walk-forward
- [x] Alertas com estados, dedup, cooldown, imutabilidade
- [x] Explicações determinísticas
- [x] Replay sem informação futura
- [x] API + Dashboard
- [ ] **Validação com dados reais de WIN/WDO** — depende do usuário fornecer histórico licenciado
- [ ] Conferência das especificações/feriados/horários com documentos oficiais da B3 (`validated: false` hoje)
