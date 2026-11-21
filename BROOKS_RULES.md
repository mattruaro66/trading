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
