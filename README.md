# ATLAS B3

Plataforma de **pesquisa, monitoramento, simulação e apoio analítico** para o mercado futuro brasileiro — **WIN** (minicontrato de Ibovespa) e **WDO** (minicontrato de dólar) — em **candles de 5 minutos**, com Price Action inspirado em Al Brooks, estatística auditável e alertas explicáveis.

> ⚠️ **O ATLAS não executa ordens.** Não há envio de ordens, integração com corretora nem copy trading. Resultados de backtest são simulações e não garantem execução nem rentabilidade.
>
> ⚠️ **O repositório não contém dados reais de mercado.** Os dados de demonstração são **sintéticos**, armazenados com o sufixo `_SINTETICO` e sinalizados em toda a interface.

## Documentação
| Documento | Conteúdo |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Módulos, modelo de dados, contratos, decisões |
| [STRATEGIES.md](STRATEGIES.md) | Critérios algorítmicos (barras, regime, H1/H2/L1/L2, Estratégia A, execução) |
| [DATA_SOURCES.md](DATA_SOURCES.md) | Fontes, formatos de importação, dados de referência a validar |
| [TEST_PLAN.md](TEST_PLAN.md) | Testes, resultados e critérios de aceitação |
| [ROADMAP.md](ROADMAP.md) | Fases e status |
| [LIVE_DATA_ARCHITECTURE.md](LIVE_DATA_ARCHITECTURE.md) · [PROVIDER_INTEGRATION.md](PROVIDER_INTEGRATION.md) · [DATA_QUALITY.md](DATA_QUALITY.md) · [LIVE_DATA_TEST_PLAN.md](LIVE_DATA_TEST_PLAN.md) | Conector de dados ao vivo |

## Executar localmente (sem Docker)

Requisitos: Python ≥ 3.12, Node ≥ 20.

Backend (SQLite por padrão em `backend/data/atlas.db`):
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn atlas.api.main:app --reload --port 8000
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```
Acesse http://localhost:5173 (a API fica em http://localhost:8000; documentação interativa em `/docs`).

Testes:
```bash
cd backend && pytest -q
```

## Executar com Docker Compose (PostgreSQL)
```bash
cp .env.example .env   # defina POSTGRES_PASSWORD
docker compose up --build
```
O Compose ainda **não foi validado** no ambiente onde o projeto foi desenvolvido (ver TEST_PLAN.md §3).

## Primeiro uso
1. **Laboratório → Importar histórico**: envie um CSV/Parquet de candles de 5 min do contrato (ex.: `WINZ26`). Formatos aceitos em [DATA_SOURCES.md](DATA_SOURCES.md). Confira o relatório de validação e, principalmente, se o timestamp do arquivo marca a abertura ou o fechamento do candle.
   - Sem dados reais à mão? **Laboratório → Dados sintéticos** gera um conjunto para testar o software (os resultados não valem nada sobre o mercado).
2. **Laboratório → Backtest**: configure perfil (scalp/day trade, ambos em 5 min), corretagem e emolumentos **da sua corretora/tabela B3** (padrão R$ 0 com aviso), slippage e fração de treino. Veja treino × teste, IC, probabilidade de evento definido, curva de capital e operações; exporte CSV. Walk-forward disponível.
3. **Replay**: reproduza candle a candle; o servidor revela um candle por vez. Clique num alerta para ver o painel analítico (seções A–G).
4. **Dados ao vivo**: hoje apenas replay de negócios **sintéticos** (simulação). A conexão Cedro está pendente — ver [PROVIDER_INTEGRATION.md](PROVIDER_INTEGRATION.md).

CLI equivalente:
```bash
python -m atlas.cli import dados.csv --symbol WIN --contract WINZ26
python -m atlas.cli backtest --symbol WIN --contract WINZ26 --profile scalp --brokerage 0.0 --fees 0.0 --out relatorio.json
```

## O que está pronto, o que depende de terceiros
| Item | Situação |
|---|---|
| Fase 1 (MVP histórico) | Implementada e testada com dados sintéticos |
| Live: interface, Candle Engine, qualidade, reconexão/recuperação, Replay/CSV providers, WS, dashboard | Implementado e testado com eventos sintéticos |
| Conexão real (Cedro ou outro) | **Depende de documentação oficial, contratação e credenciais** |
| Especificações, feriados e horários da B3 | Implementados como dados versionados; **a validar** com fontes oficiais (`validated: false`) |
| Validação estatística da Estratégia A | **Depende de dados históricos reais** de WIN/WDO |
| Macro, RAG, calibração avançada, IA generativa | Fases 2–6 (ver ROADMAP.md) |

## Estrutura
```
backend/atlas/   core · contracts · market_data · price_action · strategy · pipeline.py · backtest
                 probability · alerts · explanation · replay · knowledge · live · persistence · api · cli.py
backend/tests/   111 testes (dados sintéticos)
frontend/src/    React + TypeScript + Tailwind + Lightweight Charts
```
