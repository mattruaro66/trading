# ATLAS B3 — Fontes de Dados

## 1. Situação atual (honesta)

- **O repositório não contém dados reais de mercado.** Nenhum dado de WIN/WDO foi baixado, e nenhum resultado de desempenho foi publicado.
- Os testes usam **dados sintéticos**, gerados em código e marcados com `synthetic=True` / fonte `SYNTHETIC`. A interface exibe uma faixa de aviso sempre que um conjunto sintético está carregado.
- O script `backend/scripts/generate_synthetic.py` existe **apenas** para testar o funcionamento do sistema ponta a ponta. Os resultados de backtest sobre esses dados **não dizem nada** sobre o mercado.

## 2. Provedor inicial do MVP: arquivos do próprio usuário

O MVP funciona integralmente com arquivos históricos importados (CSV/Parquet) que o usuário obtém **legitimamente** — por exemplo, exportações da própria plataforma de negociação/gráficos contratada ou de um vendor de dados.

### Formatos aceitos pelo importador (`atlas/market_data/importers.py`)

1. **Canônico ATLAS** (CSV ou Parquet): `timestamp,open,high,low,close,volume[,trades][,contract]`
   - `timestamp` = **abertura** do candle. Com fuso explícito (ISO 8601) ou ingênuo (interpretado como America/Sao_Paulo; o relatório de importação registra essa suposição).
2. **Exportação estilo MetaTrader 5**: colunas `<DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> <TICKVOL> <VOL> <SPREAD>` (tab ou vírgula). `<VOL>` é usado como volume quando > 0; caso contrário o volume fica nulo (TICKVOL **não** é tratado como volume financeiro/contratos).
3. **Colunas em português**: `data,hora,abertura,maxima,minima,fechamento,volume[,negocios]` (separador `;` ou `,`, decimal `,` ou `.`).

Se o arquivo vier em timeframe diferente de 5 minutos, a importação é **rejeitada** (a regra do projeto proíbe outros timeframes operacionais; reamostrar 1 min → 5 min é permitido com `--resample-from-1m`, alinhado à sessão).

### Atenção à semântica do timestamp
Plataformas diferem: algumas rotulam o candle pela abertura, outras pelo fechamento. O importador tem a opção `timestamp_label = open|close`. **Usar o rótulo errado desloca tudo em 5 minutos** e introduz look-ahead; confira com um candle conhecido.

## 3. Fontes avaliadas para dados ao vivo/históricos

| Fonte | Tipo | O que se sabe | Status |
|---|---|---|---|
| Cedro Technologies (Market Data) | WebSocket/socket, pago | Documentação oficial **não pôde ser consultada** deste ambiente (domínios fora da rede permitida). Nenhum endpoint foi presumido. | Pendente — ver `PROVIDER_INTEGRATION.md` |
| B3 UP2DATA / Market Data B3 | Dados oficiais licenciados | Distribuição de dados da B3 exige licenciamento; condições e custos a consultar diretamente com a B3. | Não avaliado em detalhe |
| Plataformas de negociação das corretoras (exportação) | Arquivos | Útil para o MVP; verificar termos de uso da plataforma quanto a armazenamento/uso derivado. | Suportado via importador |
| Fontes gratuitas genéricas (ex.: índices à vista com atraso) | — | **Não** fornecem WIN/WDO 5 min com vencimento; índice à vista ≠ contrato futuro. Não usar como substituto. | Rejeitado |

Pontos a verificar com qualquer fornecedor antes de integrar: licença para armazenamento e análises derivadas; cobertura histórica intradiária; disponibilidade de negócios individuais (times & trades) com identificador/sequência; latência; mecanismo de recuperação após queda; custo mensal e por usuário; identificação dos vencimentos.

## 4. Dados de referência (`backend/data/reference/`)

| Arquivo | Conteúdo | Status |
|---|---|---|
| `contract_specs.json` | tick, valor do ponto, meses de vencimento, regra de vencimento — **versionados** com `effective_from` | `validated: false` — valores iniciais do prompt; conferir com a página de especificações da B3 |
| `b3_holidays.json` | feriados sem pregão por ano | `validated: false` — conferir com o calendário oficial da B3 do ano |
| `sessions.json` | janelas de negociação por instrumento com vigência | `validated: false` — a B3 altera horários (ex.: horário de verão dos EUA); conferir circulares vigentes |

O sistema expõe esses status em `/api/market/contracts` e no Dashboard.

## 5. Macro (Fase 4)
Fontes previstas: Banco Central (SGS, Focus, Copom), IBGE (SIDRA), Tesouro Nacional, B3; Federal Reserve, BLS, BEA, U.S. Treasury. Nenhuma está integrada no MVP; o motor de explicação declara explicitamente “contexto macro não verificado”.

## 6. Forex para validação do método (EURUSD, USDJPY)

Para validar o price action em dados reais enquanto não há histórico de WIN/WDO, o ATLAS aceita EURUSD e USDJPY exportados do MetaTrader 5 (`backend/scripts/export_mt5.py`).
- **Sessão:** só Londres/Nova York, 08:00–17:00 de Londres (com o horário de verão britânico). Candles fora dela são descartados na importação.
- **Horário:** o fuso do servidor MT5 é detectado semana a semana pela abertura do forex (domingo, 17:00 de Nova York).
- **Moeda e tamanho:** resultados em US$. O tamanho do lote é calculado em cada operação para que a perda no stop (com spread, slippage e arredondamento) não passe do orçamento (`risk_per_trade`, ex.: US$ 40 ≈ R$ 200).
- **Estratégia B:** stop como fração do range diário médio (`stop_adr_frac`); o alvo mantém a proporção 300/200.
- **Limitação:** valida o método de price action, não o comportamento específico do WIN/WDO.
