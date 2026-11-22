"""Estratégia D — "Sempre dentro" (estilo do usuário, extraído das marcações em 18 pregões reais).

MODELO EXPERIMENTAL de simulação — não executa ordens reais.

Regras (das observações do usuário):
1. Direção do dia: definida quando o rompimento da média é CONFIRMADO — barra de força que fecha além da EMA 20
   seguida de barra que também fecha além da EMA, na mesma direção ("confirmou rompimento da média").
   A direção vale até um rompimento confirmado no sentido contrário; zera no início de cada pregão.
2. Só opera a favor dessa direção ("dia de venda… só vendas").
3. Entrada: ordem stop 1 tick além de TODA barra de força a favor ("venda abaixo de toda barra de força"),
   válida no candle seguinte; stop 1 tick além da ponta oposta da barra.
4. Não opera em lateralidade (TTR): range das últimas 20 barras ≤ 4,2 ATR — limite aprendido das marcações do
   usuário (validação deixando um pregão de fora: precisão ≈ 50%, cobertura ≈ 54%).
5. Gestão: 2 lotes (scalp 1R e swing 2R); stop no zero a zero quando uma barra fecha a favor; a partir da 10ª barra,
   barra que fecha contra encerra; posição zerada no fim do pregão.
Fora do modelo (ainda): entradas com ordem limite "acima de toda barra ruim" — o simulador só tem ordens stop/mercado.
"""
from __future__ import annotations

from collections import deque

from pydantic import BaseModel, Field

from atlas.contracts.specs import get_spec
from atlas.core.types import Side, Symbol
from atlas.price_action.engine import PriceActionSnapshot
from atlas.strategy.base import Criterion, SetupEvaluation, Signal, StrategySpec, params_hash

STRATEGY_ID = "always_in_user"
STRATEGY_VERSION = "1.0.0"

SPEC = StrategySpec(
    strategy_id=STRATEGY_ID, version=STRATEGY_VERSION, name="Sempre dentro (estilo do usuário)",
    description="Depois do rompimento confirmado da média, opera só a favor, entrando além de cada barra de força.",
    context="Direção do dia confirmada por barra de força além da EMA 20 + barra de continuidade; fora de TTR.",
    pattern="Barra de força a favor (corpo ≥ 50%, fechamento no terço favorável, amplitude ≥ 0,5 ATR).",
    confirmations=("Direção do dia confirmada", "Fora de lateralidade (range 20 barras > 4,2 ATR)", "Risco dentro dos limites"),
    entry="Ordem stop 1 tick além da barra de força, válida no candle seguinte.",
    stop="1 tick além da ponta oposta da barra de força.",
    target="Lote scalp em 1R e lote swing em 2R; zero a zero quando uma barra fecha a favor.",
    horizon="Até stop, alvo, barra contra após a 10ª barra ou fim do pregão.",
    cancel_conditions=("Ordem não acionada no candle seguinte",),
    exit_conditions=("Stop", "Alvo", "Zero a zero", "Barra contra após 10 barras", "Fim da sessão"),
    references=("Marcações do usuário em 18 pregões (artefato 'Marcação de pregões').",
                "Brooks, A. Trading Price Action Trends. Wiley, 2012 (always in, barras de força)."),
)


class AlwaysInParams(BaseModel):
    profile: str = "sempre_dentro"
    scalp_r: float = Field(1.0, gt=0)
    swing_r: float = Field(2.0, gt=0)
    strong_body: float = Field(0.5, ge=0, le=1, description="Corpo mínimo da barra de força (fração da amplitude)")
    strong_close: float = Field(0.67, ge=0.5, le=1, description="Fechamento no terço favorável")
    strong_min_range_atr: float = Field(0.5, ge=0)
    ttr_lookback: int = Field(20, ge=5, le=60)
    ttr_max_range_atr: float = Field(4.2, gt=0, description="Lateralidade: range das últimas N barras ≤ isto × ATR")
    max_risk_atr: float = Field(2.5, gt=0)
    min_risk_ticks: int = Field(2, ge=1)
    be_on_favorable_close: bool = True
    manage_after_bars: int = Field(10, ge=0)
    flatten_minutes_before_close: int = Field(5, ge=0, le=60)
    time_exit_bars: int = 0

    @property
    def target_r(self) -> float:
        return self.swing_r


class AlwaysInStrategy:
    spec = SPEC
    needs_position = True

    def __init__(self, symbol: Symbol | str, params: AlwaysInParams | None = None):
        self.symbol = Symbol(symbol)
        self.params = params or AlwaysInParams()
        self.cs = get_spec(self.symbol)
        self.params_hash = params_hash({**self.params.model_dump(mode="json"), "symbol": self.symbol.value})
        self.reset()

    def resolved_params(self) -> dict:
        return self.params.model_dump(mode="json")

    def reset(self) -> None:
        self.h: deque = deque(maxlen=max(self.params.ttr_lookback, 2))
        self.direction = 0

    def _strong(self, snap, d: int) -> bool:
        m, b = snap.metrics, snap.bar
        if not m.range or not m.atr:
            return False
        body = abs(b.close - b.open) / m.range
        cp = m.close_pos if d > 0 else 1 - m.close_pos
        return (b.close - b.open) * d > 0 and body >= self.params.strong_body and cp >= self.params.strong_close \
            and m.range >= self.params.strong_min_range_atr * m.atr

    def on_snapshot(self, snap: PriceActionSnapshot, sim=None) -> list[SetupEvaluation]:
        if snap.reset_reason:
            self.reset()
        if snap.session_start:
            self.direction = 0
            self.h.clear()
        p, bar, m = self.params, snap.bar, snap.metrics
        prev = self.h[-1] if self.h else None
        ema = snap.regime.ema
        # 1. direção do dia: rompimento da média confirmado (barra de força além da EMA + barra seguinte além da EMA)
        if prev is not None and ema is not None and prev.regime.ema is not None:
            for d in (1, -1):
                if self._strong(prev, d) and (prev.bar.close - prev.regime.ema) * d > 0 \
                        and (bar.close - ema) * d > 0 and (bar.close - bar.open) * d >= 0:
                    self.direction = d
        self.h.append(snap)
        d = self.direction
        if d == 0 or not snap.data_ok or not self._strong(snap, d):
            return []
        # 4. lateralidade
        hist = list(self.h)
        rng = max(x.bar.high for x in hist) - min(x.bar.low for x in hist)
        ttr = len(hist) >= p.ttr_lookback and m.atr and rng <= p.ttr_max_range_atr * m.atr
        tick = self.cs.tick_size
        entry = self.cs.round_to_tick(bar.high + tick if d > 0 else bar.low - tick)
        stop = self.cs.round_to_tick(bar.low - tick if d > 0 else bar.high + tick)
        risk = abs(entry - stop)
        risk_ok = risk >= p.min_risk_ticks * tick and (not m.atr or risk <= p.max_risk_atr * m.atr)
        crit = (
            Criterion("D1", "Direção do dia confirmada (rompimento da média + continuidade)", True, d, "±1"),
            Criterion("D2", "Barra de força a favor", True, round(m.close_pos, 2), p.strong_close),
            Criterion("D3", f"Fora de lateralidade (range {p.ttr_lookback} barras > {p.ttr_max_range_atr:g} ATR)", not ttr,
                      round(rng / m.atr, 2) if m.atr else None, p.ttr_max_range_atr),
            Criterion("D4", "Risco entre o mínimo e o máximo", risk_ok, round(risk / m.atr, 2) if m.atr else None, p.max_risk_atr),
        )
        passed = all(c.passed for c in crit)
        side = Side.LONG if d > 0 else Side.SHORT
        signal = None
        if passed:
            t1 = entry + d * p.scalp_r * risk
            t2 = entry + d * p.swing_r * risk
            signal = Signal(
                strategy_id=STRATEGY_ID, strategy_version=STRATEGY_VERSION, params_hash=self.params_hash, profile=p.profile,
                symbol=self.symbol, contract=bar.contract, side=side, signal_bar=bar, setup_label="BARRA DE FORÇA",
                entry_price=entry, stop_price=stop, target_price=self.cs.round_to_tick(t1, "down" if d > 0 else "up"),
                risk_points=risk, risk_ticks=self.cs.price_diff_to_ticks(risk), target_r=p.swing_r, time_exit_bars=0,
                pullback_start_ts=bar.ts_open, criteria=crit,
                context={"regime": snap.regime.as_dict(), "bar": m.as_dict()},
                order_type="stop", lot_role="entry",
                extra_targets=(self.cs.round_to_tick(t2, "down" if d > 0 else "up"),), breakeven_after_first=True,
                be_on_favorable_close=p.be_on_favorable_close, manage_after_bars=p.manage_after_bars)
        return [SetupEvaluation(STRATEGY_ID, side, "BARRA DE FORÇA", bar, "candidate", crit, passed, bar.ts_open, signal)]
