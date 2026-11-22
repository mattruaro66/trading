# ATLAS B3 — Estratégias e Critérios Algorítmicos

Este documento é a **fonte de verdade dos critérios implementados**. Cada regra aqui descrita corresponde a código em `backend/atlas/price_action` e `backend/atlas/strategy`, com testes em `backend/tests`.

## Convenções de rotulagem (separação conceitual — §7.4)

Cada item é marcado com uma das categorias:

- **[CONCEITO-BROOKS]** — ideia descrita por Al Brooks em sua obra. Referências gerais (sem citações literais e sem números de página, que não foram verificados):
  - Brooks, Al. *Trading Price Action Trends*. Wiley, 2012.
  - Brooks, Al. *Trading Price Action Trading Ranges*. Wiley, 2012.
  - Brooks, Al. *Trading Price Action Reversals*. Wiley, 2012.
  - Brooks, Al. *Reading Price Charts Bar by Bar*. Wiley, 2009.
- **[INTERPRETAÇÃO-ATLAS]** — tradução própria do conceito em critérios objetivos. **Não é regra textual de Brooks.**
- **[REGRA-QUANT]** — fórmula/limiar exato implementado (parâmetro configurável, valor padrão indicado).
- **[RESULTADO]** — só é preenchido por backtests executados sobre dados reais; este documento não contém resultados.

Versão dos critérios: `price_action_engine = 1.0.0`, `second_entry_trend = 1.0.0`.

---

## 1. Métricas de barra (`price_action/bars.py`)

Para a barra *i* com O, H, L, C:

| Métrica | Fórmula [REGRA-QUANT] |
|---|---|
| `range` | H − L |
| `body` | \|C − O\| |
| `body_ratio` | body / range (0 se range = 0) |
| `upper_tail` | (H − max(O,C)) / range |
| `lower_tail` | (min(O,C) − L) / range |
| `close_pos` | (C − L) / range (0,5 se range = 0) |
| `atr` | média simples do *true range* das `atr_period` (20) barras **anteriores** (exclui a barra atual) |
| `rel_range` | range / atr (indefinido enquanto não houver `atr_period` barras) |
| `overlap_prev` | max(0, min(H,Hₚ) − max(L,Lₚ)) / range |

O ATR exclui a barra corrente para que a barra não “normalize a si mesma”. Barras só entram no cálculo depois de fechadas.

## 2. Classificação de barras

| Classe | [CONCEITO-BROOKS] | Critério [REGRA-QUANT] (padrões) |
|---|---|---|
| Doji | barra com corpo pequeno, indecisão | range = 0 **ou** body_ratio ≤ `doji_max_body` (0,30) |
| Bull trend bar | barra de alta com corpo relevante | C > O **e** body_ratio ≥ `trend_min_body` (0,50) |
| Bear trend bar | idem, baixa | C < O **e** body_ratio ≥ 0,50 |
| Weak bull / weak bear | — | demais casos com C > O / C < O |
| Fechamento na máxima | — | close_pos ≥ 0,75 |
| Fechamento na mínima | — | close_pos ≤ 0,25 |
| Inside bar | barra contida na anterior | H ≤ Hₚ e L ≥ Lₚ |
| Outside bar | barra que engloba a anterior | H > Hₚ e L < Lₚ |
| Sombra relevante | — | upper_tail ou lower_tail ≥ 0,33 |

**Barra de sinal (signal bar)** é um papel, não uma forma: é a barra fechada cuja máxima (compra) ou mínima (venda) define o preço da ordem stop hipotética. A **barra de entrada** é a barra seguinte que aciona essa ordem. Qualidade da barra de sinal: ver §5.

## 3. Regime de mercado (`price_action/regime.py`)

[CONCEITO-BROOKS] Mercado em tendência apresenta barras majoritariamente de um lado da média de 20 barras, pullbacks rasos e progresso direcional; trading range apresenta sobreposição e falta de progresso.

[INTERPRETAÇÃO-ATLAS] Medimos isso com quatro grandezas na janela de `lookback` (20) barras fechadas:

| Grandeza | Fórmula [REGRA-QUANT] |
|---|---|
| `ema` | EMA(close, 20), α = 2/(20+1), semeada pela média simples das 20 primeiras barras |
| `ema_slope_atr` | (EMAᵢ − EMAᵢ₋₅) / ATRᵢ |
| `frac_above_ema` | fração dos 20 fechamentos > EMA da própria barra |
| `efficiency` | \|Cᵢ − Cᵢ₋₂₀\| / Σ\|Cₖ − Cₖ₋₁\| (Kaufman) |
| `overlap_mean` | média de `overlap_prev` nas 20 barras |
| `buying_pressure` | Σ max(C−O,0) / Σ\|C−O\| na janela |
| `selling_pressure` | 1 − buying_pressure |

Classificação:

| Regime | Condição (padrões) |
|---|---|
| `BULL_TREND` | frac_above_ema ≥ 0,70 **e** ema_slope_atr ≥ 0,10 **e** efficiency ≥ 0,30 |
| `BEAR_TREND` | frac_above_ema ≤ 0,30 **e** ema_slope_atr ≤ −0,10 **e** efficiency ≥ 0,30 |
| `TIGHT_TRADING_RANGE` | não-tendência **e** overlap_mean ≥ 0,60 **e** efficiency < 0,15 |
| `TRADING_RANGE` | demais casos |
| `UNDEFINED` | menos de `ema_period + lookback − 1` (39) barras contíguas desde o último reinício (EMA semeada + janela completa) |

`always_in` [INTERPRETAÇÃO-ATLAS]: `LONG` se C > EMA e slope > 0; `SHORT` se C < EMA e slope < 0; senão `NONE`. É uma aproximação, não a leitura discricionária de Brooks.

**Contiguidade:** o estado do regime é reiniciado quando há lacuna de dados (candle ausente dentro da sessão), troca de contrato ou nova sessão (parâmetro `reset_on_new_session`, padrão `false` — o EMA atravessa a noite, mas a lacuna de sessão não é tratada como candle ausente).

> **Atualização (21/11/2026):** as regras de contagem, de tight trading range e de rompimento foram alinhadas ao glossário oficial do Brooks Trading Course. Ver `BROOKS_RULES.md`: pullback começa em pullback bar ou pause bar; rearma só com máxima mais baixa; limite de 20 barras; sem contagem em tight trading range.

## 4. Contagem de pernas — High 1/High 2, Low 1/Low 2 (`price_action/legs.py`)

[CONCEITO-BROOKS] Em um pullback de tendência de alta, cada vez que uma barra ultrapassa a máxima da barra anterior temos uma tentativa de retomada: a primeira é o *High 1*; se o mercado volta a lateralizar/cair e outra barra ultrapassa a máxima da anterior, temos o *High 2* (segunda entrada). Simetricamente, *Low 1/Low 2* em pullbacks de tendência de baixa.

[INTERPRETAÇÃO-ATLAS / REGRA-QUANT] Máquina de estados para o lado comprador (o lado vendedor é o espelho, trocando máximas por mínimas):

```
estado: swing_high, in_pullback, count, armed, pullback_low, pullback_start_ts, bars_in_pullback

para cada barra fechada t (p = barra anterior):
  se não in_pullback:
      swing_high = max(swing_high, H_t)
      se H_t < H_p:               # primeira máxima mais baixa → início do pullback
          in_pullback = True; count = 0; armed = True
          pullback_low = L_t; pullback_start_ts = ts_t
  senão:
      bars_in_pullback += 1
      se armed e H_t > H_p:       # tentativa de retomada → H(count+1)
          count += 1; armed = False; emitir evento H{count} na barra t
      senão se H_t ≤ H_p:         # lateral/baixa por ao menos uma barra → rearma
          armed = True
      pullback_low = min(pullback_low, L_t)
      se H_t > swing_high:        # tendência retomada: encerra o pullback
          reset (swing_high = H_t)
      se count > max_count (4) ou bars_in_pullback > max_pullback_bars (30):
          reset (pullback degenerou em range/reversão)
```

**Sinal sem look-ahead:** após processar a barra *t*, se `in_pullback` e `armed` e `count == k−1`, então a barra *t* é **candidata a barra de sinal do H{k}**: uma ordem stop hipotética 1 tick acima de Hₜ, válida apenas para a barra *t+1*. Se *t+1* ultrapassar Hₜ, a própria máquina registrará o H{k} na barra *t+1* — as duas visões são consistentes por construção (testado).

Lacunas de dados, troca de contrato e mudança de sessão reiniciam a máquina.

## 5. Estratégia A — Second Entry em tendência (`strategy/second_entry.py`)

`strategy_id = second_entry_trend`, `version = 1.0.0`, timeframe **5 min (fixo)**.

### Definição conceitual [CONCEITO-BROOKS]
Em tendência, a segunda tentativa de retomada após um pullback (H2 em alta, L2 em baixa) tende a ser uma entrada de maior qualidade que a primeira, especialmente quando a barra de sinal é forte e o pullback não destrói a tendência.

### Critérios algorítmicos (compra; venda é o espelho) [REGRA-QUANT]

Avaliados **no fechamento da barra de sinal *t***:

| # | Critério | Padrão |
|---|---|---|
| C1 | Regime em *t* = `BULL_TREND` | — |
| C2 | Barra *t* é candidata a sinal de H2 (count = 1, armed) | `entry_count = 2` |
| C3 | `close_pos` da barra de sinal ≥ `min_signal_close_pos` | 0,50 |
| C4 | Barra de sinal não é bear trend bar | `reject_opposite_trend_bar = true` |
| C5 | `rel_range` da barra de sinal em [`min_signal_rel_range`, `max_signal_rel_range`] | [0,3; 2,0] |
| C6 | Risco em ticks em [`min_risk_ticks`, `max_risk_ticks`] | WIN [4; 80], WDO [2; 40] |
| C7 | Horário de *t* entre `first_entry_time` e `last_entry_time` | 09:15–17:30 |
| C8 | Dados da janela de análise válidos (sem lacuna/qualidade inválida) | — |

### Execução hipotética
- **Entrada:** stop de compra em Hₜ + 1 tick, válida **somente** na barra *t+1*. Se *t+1* abrir acima do preço (gap), preenche na abertura. Slippage de entrada: `entry_slippage_ticks` (1).
- **Stop técnico:** `stop_mode = signal_bar` → Lₜ − 1 tick; ou `pullback_low` → mínima do pullback − 1 tick.
- **Alvo:** entrada + `target_r` × risco, arredondado ao tick (para baixo na compra — conservador).
- **Horizonte:** `time_exit_bars` barras após a entrada → saída no fechamento.
- **Encerramento de sessão:** posição zerada no fechamento da última barra com `ts_close ≥ session_close − flatten_minutes_before_close` (5).
- **Cancelamento:** se *t+1* não aciona, se *t+1* não é contígua (lacuna, outro contrato, outra sessão).

### Perfis (mesmo timeframe de 5 min)
| Perfil | `target_r` | `time_exit_bars` |
|---|---|---|
| `scalp` | 1,0 | 6 (30 min) |
| `daytrade` | 2,0 | 24 (2 h) |

### Política intrabar conservadora (documentada — §10.2)
Sem dados intrabar:
1. Na **barra de entrada**, se a mínima tocar o stop, assume-se **stop** (mesmo que o alvo também seja tocado).
2. Nas barras seguintes, se stop e alvo forem tocados na mesma barra, assume-se **stop**.
3. Gap de abertura além do stop → saída na abertura (pior que o stop) − slippage.
4. Gap de abertura além do alvo → saída **no alvo** (não na abertura mais favorável).
5. Stops, saídas por tempo e por sessão sofrem `exit_slippage_ticks` (1); alvos (ordens limitadas) não.

### Livro de ordens simulado
Backtest, replay e live usam o mesmo `TradeSimulator`, que registra as ordens do método como ordens **simuladas** (`SimOrder`, nunca enviadas a corretora/bolsa):
1. Fechamento da barra de sinal → ordem **stop** de entrada (compra acima / venda abaixo), válida só no candle seguinte; senão **expira**.
2. Entrada executada → **stop** (ordem stop) e **alvo** (ordem limitada) entram como par **OCO**.
3. Execução de uma perna cancela a outra. Saída por tempo, fim de sessão ou lacuna de dados → ordem **a mercado** que cancela o par OCO.
4. Uma posição por vez: sinais com posição/ordem ativa são registrados como ignorados.

### Invalidação
- Ordem não acionada em *t+1*.
- Pullback reiniciado (nova máxima acima do swing, `max_count`, `max_pullback_bars`).
- Perda do regime antes da barra de sinal.
- Dados inválidos/desatualizados.

### Limitações
- O regime é uma aproximação quantitativa da leitura contextual de Brooks; não diferencia spike/canal, canais largos/estreitos ou reversões maiores (Fase 2).
- Sem dados intrabar, a política conservadora tende a **subestimar** resultados com alvos curtos.
- Fila de ordens/liquidez no preço do stop não é modelada além do slippage fixo.

## 6. Estratégia B — H2/L2 + aumento no H3/L3 + momento (modelo experimental do usuário)

**Experimental:** usado para avaliar a capacidade de análise e execução do ATLAS no modelo simulado; não faz parte da versão final.
`strategy_id = h2l2_scale_momentum`. Código: `backend/atlas/strategy/h2l2_momentum.py`; execução multilote: `backend/atlas/backtest/bracket_sim.py`; testes: `backend/tests/test_strategy_h2l2_momentum.py`.

| Regra do usuário | Execução |
|---|---|
| Compra/vende em H2/L2 | Ordem stop de 1 contrato 1 tick além da barra de sinal do H2/L2, válida 1 candle |
| Aumenta a mão x1 no H3/L3 | Ordem stop de +1 contrato 1 tick além da barra de sinal do H3/L3 |
| Alvo 300 / stop 200 pontos | Por lote, a partir do preço da ordem de entrada (abertura em gap ou ordem a mercado); par OCO |
| Sempre siga a tendência | Compras só em `BULL_TREND`, vendas só em `BEAR_TREND` |
| 3 barras consecutivas → entra a favor | Na 3ª barra seguida a favor da tendência → ordem a mercado na abertura seguinte |
| Barra 2× a média das 7 anteriores → entra a favor | Barra a favor da tendência com amplitude > 2 × média das 7 anteriores → ordem a mercado na abertura seguinte |

**Lateralidade (regra do usuário):** H e L não são contados quando há 3 ou mais barras consecutivas sobrepostas (faixa de preço comum a todas). A contagem volta, do zero, só após rompimento confirmado: barra que fecha fora da faixa seguida de barra de continuidade (follow-through) que fecha além do fechamento da barra de rompimento. Sem continuidade, o rompimento falha e a lateralidade continua. Código: `backend/atlas/price_action/lateral.py` (ligado só para a estratégia B; `lateral_min_overlap = 0` = leitura literal, qualquer faixa comum).

**v1.1 — stop técnico (padrão, `stop_mode="technical"`):** stop 1 tick além da extremidade oposta da barra de sinal/ativação; alvo = 1,5 × stop (proporção 300/200), arredondado ao tick em direção à entrada. Se o stop técnico for maior que o máximo estabelecido (200 pts, ou `stop_adr_frac` × ADR no forex), a entrada **não é executada** (critério S1). Exceção: entradas de continuidade (3 BARRAS), que executam mesmo com stop maior desde que os 3 candles sejam expressivos (amplitude ≥ 1 × ATR ou volume ≥ 1,5 × média de 20 candles; no forex, volume de ticks do MT5) e o movimento esteja em tendência ou rompimento (fechamento além do extremo dos 20 candles anteriores). O comportamento da v1.0 continua disponível com `stop_mode="fixed", momentum_require_expressive=False`.

Sem janela de horário, sem exigência de posição, sem limite de contratos e sem prioridade: toda regra que disparar é executada.
Filtro adicional apenas no backtest simulado: sinais em candles com dados inválidos não são executados.
Lotes abertos são zerados no fim do pregão (escopo day trade do projeto).

### 6.1 Sistema D — hipótese derivada da estratégia B (NÃO validado)

Configuração: `H2L2MomentumParams(stop_adr_frac=0.10, enable_legs=False, enable_momentum=False, min_stop_atr=1.0, min_stop_action="widen", tech_target_r=2.0)`.
Entrada só na BARRA 2x a favor da tendência (a mercado na abertura seguinte); stop técnico além da barra, alargado até no mínimo 1 × ATR, sem execução acima de 10% do ADR; alvo 2R; risco fixo por operação; zera no fim da sessão.
Resultado em forex (jul/2025–nov/2026, 172 operações, dados já usados para escolher a regra): +0,14R sem custos (IC95 inclui zero); ≈ 0 com 0,5 pip de spread; negativo com 1 pip. Validação exigida: histórico nunca usado (2020–jun/2025) e custos reais, critérios em §6.2.

### 6.2 Critérios de aceitação para qualquer sistema (pré-definidos)
Amostra ≥ 300 operações fora da amostra; expectativa ≥ +0,10R **com custos reais**, com limite inferior do IC95 > 0; positivo em ≥ 2 de 3 ativos e em ≥ 2 de 3 terços do período; fator de lucro ≥ 1,15; drawdown máximo ≤ 15R.

## 7. Estratégias futuras (não implementadas)
B. Breakout Pullback · C. Failed Breakout em Trading Range · D. Continuação após rompimento com follow-through.
Só serão adicionadas depois que o motor da Estratégia A estiver validado com dados reais (critério do roadmap).
