"""Simulador incremental de execução hipotética (compartilhado por backtest, replay e live).

Política intrabar conservadora (STRATEGIES.md §5):
1. Barra de entrada: se a extremidade do stop for tocada → stop (mesmo que o alvo também seja).
2. Barras seguintes: stop e alvo na mesma barra → stop.
3. Gap além do stop → saída na abertura − slippage.
4. Gap além do alvo → saída no alvo.
5. Slippage em entradas, stops, tempo e sessão; alvos com ``target_slippage_ticks``.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime

from atlas.backtest.costs import CostModel
from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.contracts.specs import ContractSpec, get_spec
from atlas.core.timeframe import TZ
from atlas.core.types import Bar, Side
from atlas.market_data.validation import is_contiguous
from atlas.strategy.base import Signal


@dataclass
class Trade:
    trade_id: int
    signal: Signal
    side: Side
    entry_ts: datetime
    entry_price: float
    stop_price: float
    target_price: float
    entry_gap: bool = False
    exit_ts: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    bars_held: int = 0
    mfe_points: float = 0.0
    mae_points: float = 0.0
    ambiguous_bar: bool = False
    points: float | None = None
    gross_brl: float | None = None
    costs_brl: float | None = None
    net_brl: float | None = None
    r_multiple: float | None = None
    net_r: float | None = None

    @property
    def is_open(self) -> bool:
        return self.exit_ts is None

    def as_dict(self) -> dict:
        s = self.signal
        return {
            "trade_id": self.trade_id, "symbol": s.symbol.value, "contract": s.contract, "side": self.side.value,
            "setup": s.setup_label, "strategy_id": s.strategy_id, "strategy_version": s.strategy_version,
            "profile": s.profile, "signal_ts": s.signal_ts.isoformat(), "entry_ts": self.entry_ts.isoformat(),
            "entry_price": self.entry_price, "stop_price": self.stop_price, "target_price": self.target_price,
            "risk_points": s.risk_points, "entry_gap": self.entry_gap,
            "exit_ts": self.exit_ts.isoformat() if self.exit_ts else None, "exit_price": self.exit_price,
            "exit_reason": self.exit_reason, "bars_held": self.bars_held, "mfe_points": self.mfe_points,
            "mae_points": self.mae_points, "ambiguous_bar": self.ambiguous_bar, "points": self.points,
            "gross_brl": self.gross_brl, "costs_brl": self.costs_brl, "net_brl": self.net_brl,
            "r_multiple": self.r_multiple, "net_r": self.net_r,
            "regime": s.context.get("regime", {}).get("regime"),
            "hour": s.signal_bar.ts_close.astimezone(TZ).hour,
        }


@dataclass(frozen=True)
class ExecEvent:
    kind: str  # order_placed | order_expired | order_cancelled | filled | closed | signal_skipped
    ts: datetime
    signal: Signal
    trade: Trade | None = None
    reason: str | None = None


@dataclass
class _Pending:
    signal: Signal
    placed_after: Bar


@dataclass
class TradeSimulator:
    symbol: str
    costs: CostModel = field(default_factory=CostModel)
    flatten_minutes_before_close: int = 5
    calendar: TradingCalendar | None = None

    def __post_init__(self) -> None:
        self.cal = self.calendar or load_calendar()
        self.spec: ContractSpec = get_spec(self.symbol)
        self.pending: _Pending | None = None
        self.open_trade: Trade | None = None
        self.closed: list[Trade] = []
        self.prev_bar: Bar | None = None
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------
    def submit(self, signal: Signal, signal_bar: Bar) -> ExecEvent:
        if self.open_trade is not None or self.pending is not None:
            return ExecEvent("signal_skipped", signal_bar.ts_close, signal, reason="position_or_order_active")
        self.pending = _Pending(signal, signal_bar)
        return ExecEvent("order_placed", signal_bar.ts_close, signal)

    def _slip(self, ticks: float) -> float:
        return self.spec.ticks(ticks)

    def _session_flatten(self, bar: Bar) -> bool:
        s = self.cal.session(bar.symbol, bar.ts_open.astimezone(TZ).date())
        if s is None:
            return True
        from datetime import timedelta
        return bar.ts_close >= s.close - timedelta(minutes=self.flatten_minutes_before_close)

    def on_bar(self, bar: Bar) -> list[ExecEvent]:
        """Processa um candle fechado. Deve ser chamado ANTES de submeter sinais gerados neste mesmo candle."""
        events: list[ExecEvent] = []
        prev = self.prev_bar
        contiguous = prev is not None and is_contiguous(prev, bar)

        # Posição aberta e lacuna/troca de contrato/nova sessão → encerra no último preço conhecido
        if self.open_trade is not None and not contiguous:
            t = self.open_trade
            px = prev.close - t.side.sign * self._slip(self.costs.exit_slippage_ticks)
            self._close(t, prev.ts_close, px, "data_gap_exit")
            events.append(ExecEvent("closed", prev.ts_close, t.signal, t, "data_gap_exit"))

        if self.pending is not None:
            pend = self.pending
            self.pending = None
            if not contiguous or pend.placed_after is not prev:
                events.append(ExecEvent("order_cancelled", bar.ts_open, pend.signal, reason="next_bar_not_contiguous"))
            else:
                ev = self._try_fill(pend.signal, bar)
                events.extend(ev)

        elif self.open_trade is not None:
            events.extend(self._manage(self.open_trade, bar, entry_bar=False))

        self.prev_bar = bar
        return events

    def _try_fill(self, s: Signal, bar: Bar) -> list[ExecEvent]:
        sign = s.side.sign
        slip_in = self._slip(self.costs.entry_slippage_ticks + self.costs.spread_ticks)
        gap = False
        if s.side is Side.LONG:
            if bar.open >= s.entry_price:
                fill, gap = bar.open + slip_in, True
            elif bar.high >= s.entry_price:
                fill = s.entry_price + slip_in
            else:
                return [ExecEvent("order_expired", bar.ts_close, s, reason="not_triggered")]
        else:
            if bar.open <= s.entry_price:
                fill, gap = bar.open - slip_in, True
            elif bar.low <= s.entry_price:
                fill = s.entry_price - slip_in
            else:
                return [ExecEvent("order_expired", bar.ts_close, s, reason="not_triggered")]
        fill = self.spec.round_to_tick(fill, "up" if sign > 0 else "down")
        t = Trade(next(self._ids), s, s.side, bar.ts_open, fill, s.stop_price, s.target_price, entry_gap=gap)
        self.open_trade = t
        events = [ExecEvent("filled", bar.ts_open, s, t)]
        events.extend(self._manage(t, bar, entry_bar=True))
        return events

    def _manage(self, t: Trade, bar: Bar, entry_bar: bool) -> list[ExecEvent]:
        sign = t.side.sign
        slip_out = self._slip(self.costs.exit_slippage_ticks + self.costs.spread_ticks)
        slip_tgt = self._slip(self.costs.target_slippage_ticks)
        t.bars_held += 1
        fav = (bar.high - t.entry_price) if sign > 0 else (t.entry_price - bar.low)
        adv = (t.entry_price - bar.low) if sign > 0 else (bar.high - t.entry_price)
        t.mfe_points = max(t.mfe_points, fav)
        t.mae_points = max(t.mae_points, adv)

        stop_hit = bar.low <= t.stop_price if sign > 0 else bar.high >= t.stop_price
        tgt_hit = bar.high >= t.target_price if sign > 0 else bar.low <= t.target_price
        exit_px = reason = None
        if not entry_bar:
            gap_stop = bar.open <= t.stop_price if sign > 0 else bar.open >= t.stop_price
            gap_tgt = bar.open >= t.target_price if sign > 0 else bar.open <= t.target_price
            if gap_stop:
                exit_px, reason = bar.open - sign * slip_out, "stop_gap"
            elif gap_tgt:
                exit_px, reason = t.target_price - sign * slip_tgt, "target_gap"
        if reason is None:
            if stop_hit:
                exit_px, reason = t.stop_price - sign * slip_out, "stop"
                if tgt_hit:
                    t.ambiguous_bar = True
            elif tgt_hit:
                exit_px, reason = t.target_price - sign * slip_tgt, "target"
            elif t.bars_held >= t.signal.time_exit_bars:
                exit_px, reason = bar.close - sign * slip_out, "time_exit"
            elif self._session_flatten(bar):
                exit_px, reason = bar.close - sign * slip_out, "session_end"
        if reason is None:
            return []
        exit_px = self.spec.round_to_tick(exit_px, "down" if sign > 0 else "up")
        self._close(t, bar.ts_close, exit_px, reason)
        return [ExecEvent("closed", bar.ts_close, t.signal, t, reason)]

    def _close(self, t: Trade, ts: datetime, px: float, reason: str) -> None:
        t.exit_ts, t.exit_price, t.exit_reason = ts, px, reason
        t.points = round((px - t.entry_price) * t.side.sign, 10)
        q = self.costs.contracts
        t.gross_brl = self.spec.pnl_brl(t.points, q)
        t.costs_brl = self.costs.round_trip_fees()
        t.net_brl = round(t.gross_brl - t.costs_brl, 2)
        risk_brl = self.spec.pnl_brl(t.signal.risk_points, q)
        t.r_multiple = round(t.points / t.signal.risk_points, 4) if t.signal.risk_points else None
        t.net_r = round(t.net_brl / risk_brl, 4) if risk_brl else None
        self.closed.append(t)
        self.open_trade = None
