# Regras de Price Action (Al Brooks) aplicadas aos trading plans do ATLAS

Fonte principal (lida em 21/11/2026): **Brooks Trading Course — Price Action Trading Glossary**
<https://www.brookstradingcourse.com/price-action-trading-terms-glossary/>.
Complementos: [Ask Al: Breakouts, when to exit or hold?](https://www.brookstradingcourse.com/ask-al/breakouts/),
[fórum: Low 1-2-3 e High 1-2-3](https://www.brookstradingcourse.com/support-forum/general-trading-discussion/problem-understanding-low-1-2-3-and-high-1-2-3/)
e trechos do livro *Trading Price Action Trading Ranges* (Wiley, 2012) retornados por busca.

As definições abaixo são **resumos em português** (não são citações completas). Cada regra mostra:
o que o Brooks descreve → como o ATLAS aplica → estado.

| # | Conceito (Brooks) | Regra no ATLAS | Estado |
|---|---|---|---|
| 1 | **High 1 / High 2 / H3 / H4**: H1 é uma barra com máxima acima da barra anterior num bull flag (ou perto do fundo de um trading range). Depois de uma barra com **máxima mais baixa** (uma ou várias barras depois), a próxima barra da correção que supera a máxima da anterior é o H2; a 3ª e a 4ª são H3 e H4. Low 1–4 é o espelho. | `legs.py`: a contagem sobe quando a máxima supera a da barra anterior e só rearma depois de uma barra com máxima **estritamente** mais baixa. | Aplicado |
| 2 | **Pullback bar**: numa perna de alta, barra com mínima abaixo da anterior. **Pause bar**: máxima igual ou abaixo da anterior; também é um tipo de pullback. | O pullback começa numa pullback bar **ou** numa pause bar. | Aplicado |
| 3 | **Minor trend reversal**: se o pullback vai acumulando barras, depois de cerca de 20 barras ele vira um trading range. | Pullback com mais de 20 barras reinicia a contagem. | Aplicado |
| 4 | **Tight trading range**: barras com muita sobreposição, em que a maioria das reversões é pequena demais para operar com ordens stop. **Barbwire**: tipo de tight TR; iniciantes devem parar de operar até um rompimento claro. Com range diário médio de 10–15 pontos no Emini, um range de até ~3 pontos é provavelmente tight. | 3+ barras com fechamento dentro da barra anterior (sua definição), numa faixa de até 25% do range diário médio dos 5 pregões anteriores. Nesse estado **não há contagem de H/L**. | Aplicado (todas as estratégias) |
| 5 | **Breakout bar**: a barra que cria o rompimento, normalmente uma trend bar forte. O rompimento é mais forte quando o fechamento fica além do ponto rompido. | Rompimento do tight TR exige fechamento fora da faixa e corpo na direção do rompimento. | Aplicado |
| 6 | **Follow-through**: barra(s) que estendem o movimento. Após um rompimento de alta, os touros querem que a barra seguinte tenha corpo de alta (os ursos querem corpo de baixa, sinal de falha). | Confirmação: a barra seguinte tem corpo na mesma direção e fecha fora da faixa. Sem isso, o rompimento falha e a lateralidade continua. A contagem recomeça do zero após a confirmação. | Aplicado |
| 7 | **Signal bar**: a barra imediatamente antes da barra em que a ordem é executada. **Entry bar**: a barra em que a entrada acontece. Entrada típica: stop 1 tick além da signal bar. | Ordem stop 1 tick além da barra de sinal, válida por 1 candle. | Já existia |
| 8 | **With trend**: na dúvida, a direção do sinal mais recente de 5 min é a tendência; se a maioria das últimas 10–20 barras está acima da média móvel, os setups a favor são de compra. Gráficos do Brooks usam EMA de 20 barras. **Countertrend** é estratégia perdedora para a maioria. | Regime pela EMA20 (fração de fechamentos acima/abaixo, inclinação, eficiência). Sem entradas contra a tendência. | Já existia |
| 9 | **Wedge flag**: pullback de três impulsos (ex.: H3 em tendência de alta); por ser a favor da tendência, entrar no primeiro sinal. | O H3/L3 da estratégia B é executado no primeiro sinal. | Já existia (estratégia B) |
| 10 | **Trader's equation**: probabilidade × recompensa precisa superar (1 − probabilidade) × risco. Sem certeza, assumir 40–50% de acerto e buscar recompensa ≥ 2× o risco; com confiança (60%), alvo de 1× o risco. **Likely/probably/usually** = pelo menos 60%. | Relatórios mostram a taxa de acerto de empate de cada plano. Com stop 200 e alvo 300, mais o slippage, o empate é 41,6%. | Em relatório |
| 11 | **Scalp**: quando o lucro é menor que o risco, é preciso acertar pelo menos ~70%. Operar apenas com recompensa pelo menos igual ao risco, salvo grande habilidade. | Aviso nos relatórios quando alvo < stop. | A implementar |
| 12 | **Micro channel / tight channel**: canal muito estreito, sem pullbacks ou com 1–2 pequenos (1–3 barras). Não se opera contra ele. | Nenhuma entrada contra a tendência (regra 8). Detecção explícita de micro channel: a implementar. | Parcial |
| 13 | **Second entry / second signal**: segunda vez, poucas barras depois, com a mesma lógica da primeira. **Wedge reversal** é contra a tendência: melhor esperar o segundo sinal. | H2/L2 são a segunda entrada. Reversões não são operadas. | Já existia |
| 14 | **Failed failure**: rompimento que falha e depois volta a romper na direção original vira breakout pullback; por ser segundo sinal, é mais confiável. | A implementar (estratégia de breakout pullback). | A implementar |

## O que mudou no motor
- `atlas/price_action/legs.py`: início do pullback (pullback bar ou pause bar), rearmamento só com máxima mais baixa, limite de 20 barras.
- `atlas/price_action/lateral.py`: tight trading range com limite de altura pelo range diário, rompimento com corpo e follow-through com corpo.
- `atlas/price_action/engine.py`: filtro de tight trading range **ligado por padrão para todos os trading plans** (A e B).

## Limitações
- O Brooks avisa que não há regras rígidas, só diretrizes, e que a leitura depende do contexto. Estas regras são uma tradução objetiva e verificável dessas diretrizes, não a leitura discricionária dele.
- O limite de 25% do range diário vem de um exemplo do livro (3 pontos para um range de 10–15 pontos), não de uma regra explícita.

## Estratégia C — Método Brooks (regras de execução)

`backend/atlas/strategy/brooks_method.py` transforma os conceitos em 20 setups executáveis (S1–S20), com filtros de contexto e gestão do Brooks. Testes: `backend/tests/test_strategy_brooks_method.py`. **Experimental:** serve para validar o método em simulação.

| Setup | Conceito do glossário | Regra |
|---|---|---|
| S1 | High/Low 2, second entry | H2/L2 a favor da tendência e do always in |
| S2 | Small pullback trend, strong trend | H1/L1 com ≥90% dos fechamentos do lado da EMA ou após barra-surpresa |
| S3/S4 | Wedge flag, H3/H4 | H3/L3 e H4/L4 a favor, no 1º sinal |
| S5 | Trading range: H1/H2 perto do fundo | Range comum (não tight): H1/H2 no terço inferior (L1/L2 no superior) |
| S6 | Double bottom bull flag / double top bear flag | Mínima a ≤0,15 ATR de um swing low de 3–20 barras atrás, barra de alta |
| S7 | Micro double bottom/top, ledge | Mínimas iguais (±1 tick) em barras seguidas, a favor |
| S8 | Breakout pullback | H1/L1 até 5 barras após rompimento confirmado; swing mira medida do range |
| S9 | Failed failure | Rompimento que falhou retoma com trend bar forte em até 3 barras |
| S10 | Failed breakout / five-tick failure | Em range: excede o extremo de 40 barras e fecha de volta com barra de reversão |
| S11 | First moving average gap bar | 1ª barra sem tocar a EMA no pullback de uma tendência com ≥10 barras |
| S12 | 20 moving average gap bars | 1º toque na EMA após ≥20 barras sem tocá-la |
| S13 | Wedge reversal (2º sinal) | Três swings consecutivos e L2/H2 contra a tendência |
| S14 | Major trend reversal | Trend bar forte rompe a EMA e, em 3–40 barras, o extremo é testado com barra de reversão |
| S15 | ii / ioi (breakout mode) | Entrada a favor do always in |
| S16 | Gap reversal / opening reversal | 1º candle com gap; ordem além da mínima/máxima dele |
| S17 | Trend from the open | Duas trend bars fortes nas 2 primeiras barras → a mercado |
| S18 | Spike, buy/sell the close | Duas trend bars fortes rompendo as 10 barras anteriores → a mercado |
| S19 | Surprise bar (2ª perna) | H1/L1 até 10 barras após barra ≥2 ATR |
| S20 | Micro channel | H1/L1 até 3 barras após micro canal de ≥5 barras |

**Filtros:** tight trading range, micro canal contrário, clímax (barra ≥3 ATR em tendência madura → 10 barras sem entradas a favor), equação do trader (risco de 2 ticks a 2,5 ATR), dados válidos.

**Gestão:** stop 1 tick além da barra de sinal; lote scalp 1R + lote swing 2R ou medida do movimento; stop do swing no zero a zero após o scalp; posição zerada no fim do pregão.

**Fora das regras de execução**, por não serem setups: participantes (instituições, HFT), notícias, filosofia (não há regras fixas), tempo gráfico maior/menor (o projeto opera só em 5 min), tamanho de posição e conta.


## Estratégia C v1.2 — regras da revisão do usuário (20 operações perdedoras comentadas)

| Regra | Comentário do usuário que a originou | Tradução |
|---|---|---|
| U1 | "Operação em TTR é sempre erro" (S3, S4, S5, S6, S7, S11, S15) | Sem entradas com EMA plana (|ΔEMA 10 barras| < 0,3 ATR) e ≥ 3 cruzamentos do preço com a média em 20 barras, nem em tight trading range |
| U2 | "Comprou acima de barra negativa — erro crasso" (S16, S19), "sinal fraco" (S4, S17) | Barra de sinal com corpo e fechamento a favor (fechamento ≥ 60% na direção) |
| U3 | "Vendeu suporte", "venda baixo numa lateralidade", "comprando topo" (S3, S8, S9) | Espaço ≥ 1R até o próximo swing, máx/mín do dia ou do dia anterior |
| U4 | "Sinal próximo à média", "L1 na média", "venda no fim do movimento (clímax)" (S2, S4, S12, S18) | Entradas a favor no máximo a 1,5 ATR da EMA |
| U5 | "Fim do dia, baixo volume e amplitude" (S14, S19) | Sem entradas nos últimos 60 min da sessão |
| U6 | "Esperaria o rompimento da média e uma primeira perna" (S10) | Reversões só depois de uma perna que fecha além da média com barra de força |

`BrooksMethodParams(user_rules=False)` mantém a v1.1. Resultado e limitações: ver relatório da sessão; as 20 operações revisadas passam a ser bloqueadas, mas a expectativa do conjunto não melhorou (as regras cortam ganhadoras e perdedoras na mesma proporção).


## Estratégia C v1.4 — 2ª revisão do usuário (20 perdas críticas comentadas)

| Regra | Comentário do usuário | Tradução |
|---|---|---|
| Pernas | "Aguardaria o segundo sinal (que corresponde ao fim da segunda perna)" (T20) | `LegParams.two_legs`: depois do H1/L1, só rearma com nova mínima/máxima do pullback (2ª perna) |
| U7 | "Compra contra um canal estreito de baixa e contra a tendência" (T03–T07) | Sem entradas quando 9+ das últimas 10 barras fecham do outro lado da média e ela anda ≥ 0,5 ATR contra |
| U8 | "Podia esperar romper a linha de tendência com confirmação e vender depois" (T05) | Reversões só como 2º sinal (contagem ≥ 1) depois de barra de força que fecha além da média |
| U9 | "Comprou uma barra ruim contra uma força enorme de baixa da barra anterior" (T07) | Barra anterior não pode ser barra de força contra e maior que a de sinal |
| U10 | "Vendeu no final da tendência", "terceira perna" (T08, revisão 1 S1) | A favor só até a 2ª perna da tendência |

Lateralidade (TTR): nenhuma medida simples (range de 6–20 barras em ATR, sobreposição, inclinação da EMA, cruzamentos) separa os 12 casos que o usuário chamou de TTR dos 19 que não chamou; U1 continua sendo uma aproximação fraca.
