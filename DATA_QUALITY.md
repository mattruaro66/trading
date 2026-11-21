# ATLAS B3 — Qualidade de Dados

Princípio: **dado inválido ou desatualizado impede alertas que dependem dele. Lacunas nunca são preenchidas com preços fictícios.**

## 1. Histórico importado (`atlas/market_data/validation.py`)

| Verificação | Ação |
|---|---|
| Timestamps fora de ordem | Reordena + aviso |
| Duplicidade idêntica | Mantém uma + aviso |
| Duplicidade conflitante | Rejeita todas as versões |
| Dia sem pregão (calendário) | Rejeita |
| Fora da sessão / desalinhado à grade de 5 min | Rejeita |
| Preço ausente/≤0, OHLC inconsistente, volume negativo | Rejeita |
| Preço fora do tick | Mantém como `suspect` |
| Candles ausentes dentro da sessão | Reporta lacuna; não preenche |
| Ano sem cobertura de feriados | Aviso `calendar_coverage` |
| Intervalo ≠ 5 min | Rejeita o arquivo (1 min só com reamostragem explícita) |

Cada importação registra hash SHA-256 do arquivo, formato detectado e suposições (fuso, rótulo do timestamp, origem do volume).

## 2. Pipeline analítico
- Candles `suspect`/`invalid` na janela de análise → `data_ok=False` → critério C8 falha → sem sinal.
- Lacuna, troca de contrato → reinício completo do motor (regime volta a `UNDEFINED`).
- Nova sessão → contagem de pernas reiniciada; posição/ordem pendente não atravessa a noite.

## 3. Dados ao vivo (`atlas/live/candle_engine.py`)

| Situação | Política |
|---|---|
| Cotação (bid/ask) | Ignorada para OHLCV |
| Duplicidade | Por `trade_id`; sem ele, por `sequence_number`; sem ambos, **não** descarta (pode ser legítimo) e sinaliza `no_trade_identity` |
| Fora de ordem dentro do candle aberto | Aceito; abertura/fechamento definidos por (timestamp da bolsa, sequência) |
| Lacuna de sequência | `DataGapDetected(sequence_gap)` + candle `suspect` |
| Fechamento | Quando a marca d'água (maior timestamp da bolsa ou relógio) ≥ fim do candle + `close_grace` (2 s) |
| Evento atrasado para candle fechado | `CandleInvalidated(late_event)` + lacuna; só volta a valer via reconstrução |
| Candle sem negócios | `DataGapDetected(no_trades)`; nenhum candle criado |
| Antes do primeiro negócio recebido | `DataGapDetected(no_data_received)` (não bloqueia alertas) |
| Conexão degradada durante o candle | Candle fechado como `INVALID` |
| Quantidade ausente | `volume=None` + flag `volume_unavailable` |
| Fora da sessão / outro contrato | Rejeitado e contabilizado |

## 4. Suspensão e retomada de alertas
Bloqueiam: `ConnectionDegraded`, `CandleInvalidated`, `DataGapDetected` (exceto `no_data_received`), candle não-OK na janela.
Retomada: conexão `connected` **e** motor não degradado **e** um `CandleClosed` com qualidade OK. Sinais suprimidos são registrados em `AlertEngine.suppressed` com o motivo.

## 5. Métricas expostas (`GET /api/market/status`)
Latência última/p50/p95, último evento (relógio e bolsa), tentativas de reconexão, contadores do Candle Engine (aceitos, duplicados, atrasados, lacunas de sequência, fora de ordem, rejeitados), motivo do bloqueio de alertas.

## 6. Limitações
- Fluxo de ordens (agressão, livro) não é calculado: o modelo atual usa apenas negócios/OHLCV.
- `close_grace` fixo (2 s) — calibrar com a latência real do fornecedor.
- Política de candle sem negócios assume que ausência de negócio em 5 min é anômala para WIN/WDO; leilões/interrupções aparecerão como lacunas.
