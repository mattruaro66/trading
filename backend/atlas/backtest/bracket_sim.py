"""Simulador de execução com VÁRIOS LOTES (aumento de mão), cada lote com stop e alvo próprios em OCO.

Usado por estratégias que adicionam contratos (ex.: H2 + aumento no H3). Ordens são SIMULADAS
(``SimOrder``); nada é enviado a corretora/bolsa. Mesma política conservadora do ``TradeSimulator``:
* candle de entrada: stop/alvo só depois da ativação, pelo caminho O→L→H→C (alta) ou O→H→L→C (baixa);
* stop e alvo no mesmo candle → stop;
* gap além do stop → saída na abertura; gap além do alvo → saída no alvo;
* fim da sessão / lacuna de dados → saída a mercado de todos os lotes.

Preço de referência do bracket (stop/alvo em pontos fixos): o preço da ordem de entrada (ordens stop)
ou a abertura do candle (ordens a mercado e entradas em gap) — antes do slippage.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from atlas.backtest.costs import CostModel
from atlas.backtest.execution import EXIT_REASON_PT, ExecEvent, SimOrder, Trade, entry_bar_path
from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.contracts.specs import get_spec
from atlas.core.timeframe import TZ
from atlas.core.types import Bar, Side
from atlas.market_data.validation import is_contiguous
from atlas.strategy.base import Signal


@dataclass
class _Pending:
    signal: Signal
    placed_after: Bar
    group: int
    order: SimOrder
    qty: float = 1.0


@dataclass
class BracketSimulator:
    symbol: str
    costs: CostModel = field(default_factory=CostModel)
    flatten_minutes_before_close: int = 5
    calendar: TradingCalendar | None = None
    max_lots: int | None = None  # None = sem limite de contratos

    def __post_init__(self) -> None:
        self.cal = self.calendar or load_calendar()
        self.spec = get_spec(self.symbol)
        self.pending: list[_Pending] = []
        self.lots: list[Trade] = []
        self.closed: list[Trade] = []
        self.orders: list[SimOrder] = []
        self.prev_bar: Bar | None = None
        self.realized_brl = 0.0
        self._ids = itertools.count(1)
        self._order_ids = itertools.count(1)
        self._groups = itertools.count(1)
        self._working: dict[int, dict[str, SimOrder]] = {}  # grupo → função → ordem
        self._lot_group: dict[int, int] = {}  # trade_id → grupo
        self._siblings: dict[int, list[Trade]] = {}  # trade_id → lotes do mesmo sinal

    # ------------------------------------------------------------------ estado
    @property
    def open_trade(self) -> Trade | None:  # compatibilidade com o TradeSimulator
        return self.lots[0] if self.lots else None

    @property
    def side(self) -> Side | None:
        if self.lots:
            return self.lots[0].side
        if self.pending:
            return self.pending[0].signal.side
        return None

    def exposure(self) -> int:
        return len(self.lots) + len(self.pending)

    def lots_from(self, setup_key: str) -> list[Trade]:
        return [t for t in self.lots if t.signal.setup_key == setup_key]

    def position(self, last: Bar | None = None) -> dict:
        bar = last or self.prev_bar
        working = [o.as_dict() for g in self._working.values() for o in g.values()]
        base = {"realized_brl": round(self.realized_brl, 2), "closed_trades": len(self.closed), "working_orders": working}
        if not self.lots:
            return {**base, "state": "pending_entry" if self.pending else "flat"}
        net_qty = round(sum(t.side.sign * t.qty for t in self.lots), 8)
        net = net_qty
        side = Side.LONG if net > 0 else Side.SHORT if net < 0 else self.lots[0].side
        mark = bar.close if bar else self.lots[0].entry_price
        avg = sum(t.entry_price for t in self.lots) / len(self.lots)
        pts = round(sum((mark - t.entry_price) * t.side.sign for t in self.lots), 10)
        unreal = round(sum(self.spec.pnl_money((mark - t.entry_price) * t.side.sign, t.qty, mark) for t in self.lots), 2)
        return {**base, "state": "long" if net > 0 else "short" if net < 0 else "hedged", "qty": abs(net_qty),
                "lots_open": len(self.lots),
                "entry_price": round(avg, 2), "mark_price": mark, "unrealized_points": pts,
                "unrealized_brl": unreal,
                "stop_price": self.lots[-1].stop_price, "target_price": self.lots[-1].target_price,
                "bars_held": max(t.bars_held for t in self.lots), "setup": "+".join(t.signal.setup_label for t in self.lots),
                "lots": [{"setup": t.signal.setup_label, "side": t.side.value, "entry": t.entry_price, "stop": t.stop_price, "target": t.target_price}
                         for t in self.lots]}

    # ------------------------------------------------------------------ ordens
    def _order(self, group: int, role: str, action: str, otype: str, price: float | None, ts: datetime, setup: str,
               note: str = "", qty: float | None = None) -> SimOrder:
        o = SimOrder(next(self._order_ids), group, role, action, otype, self.costs.contracts if qty is None else qty,
                     price, ts, note=note, setup=setup)
        self.orders.append(o)
        self._working.setdefault(group, {})[role] = o
        return o

    def _finish(self, group: int, role: str, status: str, ts: datetime, fill: float | None = None, note: str = ""):
        o = self._working.get(group, {}).pop(role, None)
        if o is None:
            return None
        o.status, o.done_ts, o.fill_price = status, ts, fill
        if note:
            o.note = note
        if not self._working.get(group):
            self._working.pop(group, None)
        return o

    @staticmethod
    def _snap(*orders) -> tuple:
        return tuple(o.as_dict() for o in orders if o is not None)

    def submit(self, signal: Signal, signal_bar: Bar) -> ExecEvent:
        if self.max_lots is not None and self.exposure() >= self.max_lots:
            return ExecEvent("signal_skipped", signal_bar.ts_close, signal, reason="max_lots")
        n_lots = 1 + len(signal.extra_targets)
        risk_pts = signal.stop_points if signal.stop_points is not None else signal.risk_points
        qty = self.costs.quantity(self.spec, risk_pts, signal.entry_price, n_lots)
        if qty <= 0:
            return ExecEvent("signal_skipped", signal_bar.ts_close, signal, reason="risk_exceeds_budget_at_min_size")
        g = next(self._groups)
        action = "buy" if signal.side is Side.LONG else "sell"
        role = "entry"
        if signal.order_type == "market":
            o = self._order(g, role, action, "market", None, signal_bar.ts_close, signal.setup_label,
                            ("aumento de mão · " if signal.lot_role == "add" else "") + "a mercado na abertura do próximo candle",
                            qty=round(qty * n_lots, 8))
        else:
            o = self._order(g, role, action, "stop", signal.entry_price, signal_bar.ts_close, signal.setup_label,
                            ("aumento de mão · " if signal.lot_role == "add" else "") + "válida somente no próximo candle de 5 min",
                            qty=round(qty * n_lots, 8))
        self.pending.append(_Pending(signal, signal_bar, g, o, qty))
        return ExecEvent("order_placed", signal_bar.ts_close, signal, orders=self._snap(o))

    # ------------------------------------------------------------------ candle
    def _flatten_time(self, bar: Bar) -> bool:
        s = self.cal.session(bar.symbol, bar.ts_open.astimezone(TZ).date())
        return s is None or bar.ts_close >= s.close - timedelta(minutes=self.flatten_minutes_before_close)

    def on_bar(self, bar: Bar) -> list[ExecEvent]:
        events: list[ExecEvent] = []
        prev = self.prev_bar
        contiguous = prev is not None and is_contiguous(prev, bar)
        if self.lots and not contiguous:
            for t in list(self.lots):
                px = self.spec.round_to_tick(prev.close - t.side.sign * self.spec.ticks(self.costs.exit_slippage_ticks),
                                             "down" if t.side is Side.LONG else "up")
                events.append(self._exit(t, prev.ts_close, px, "data_gap_exit"))
        existing = list(self.lots)
        pend, self.pending = self.pending, []
        for p in pend:
            if not contiguous or p.placed_after is not prev:
                o = self._finish(p.group, "entry", "cancelled", bar.ts_open, note="candle seguinte ausente/não contíguo")
                events.append(ExecEvent("order_cancelled", bar.ts_open, p.signal, reason="next_bar_not_contiguous",
                                        orders=self._snap(o)))
            else:
                events.extend(self._try_fill(p, bar))
        for t in existing:
            if t in self.lots:
                ev = self._manage(t, bar, entry_bar=False)
                if ev:
                    events.append(ev)
        self.prev_bar = bar
        return events

    def _try_fill(self, p: _Pending, bar: Bar) -> list[ExecEvent]:
        s = p.signal
        sign = s.side.sign
        slip = self.spec.ticks(self.costs.entry_slippage_ticks + self.costs.spread_ticks)
        gap = False
        if s.order_type == "market":
            ref = bar.open
        elif s.side is Side.LONG:
            if bar.open >= s.entry_price:
                ref, gap = bar.open, True
            elif bar.high >= s.entry_price:
                ref = s.entry_price
            else:
                ref = None
        else:
            if bar.open <= s.entry_price:
                ref, gap = bar.open, True
            elif bar.low <= s.entry_price:
                ref = s.entry_price
            else:
                ref = None
        if ref is None:
            o = self._finish(p.group, "entry", "expired", bar.ts_close, note="preço não atingido no candle seguinte")
            return [ExecEvent("order_expired", bar.ts_close, s, reason="not_triggered", orders=self._snap(o))]
        fill = self.spec.round_to_tick(ref + sign * slip, "up" if sign > 0 else "down")
        if s.stop_points is not None:
            stop = self.spec.round_to_tick(ref - sign * s.stop_points)
            target = self.spec.round_to_tick(ref + sign * s.target_points)
        else:
            stop, target = s.stop_price, s.target_price
        exit_action = "sell" if s.side is Side.LONG else "buy"
        entry = self._finish(p.group, "entry", "filled", bar.ts_open, fill, "gap de abertura" if gap else "")
        targets = [target] + [self.spec.round_to_tick(x) for x in s.extra_targets]
        new_lots, snaps = [], [entry]
        for k, tgt in enumerate(targets):
            g = p.group if k == 0 else next(self._groups)
            t = Trade(next(self._ids), s, s.side, bar.ts_open, fill, stop, tgt, entry_gap=gap, qty=p.qty)
            t.lot = "scalp" if (k == 0 and len(targets) > 1) else ("swing" if k > 0 else "único")
            self.lots.append(t)
            self._lot_group[t.trade_id] = g
            tag = "" if len(targets) == 1 else (" · lote scalp" if k == 0 else f" · lote swing {k}")
            snaps.append(self._order(g, "stop_loss", exit_action, "stop", stop, bar.ts_open, s.setup_label, "OCO com o alvo deste lote" + tag, qty=p.qty))
            snaps.append(self._order(g, "take_profit", exit_action, "limit", tgt, bar.ts_open, s.setup_label, "OCO com o stop deste lote" + tag, qty=p.qty))
            new_lots.append(t)
        for t in new_lots:
            self._siblings[t.trade_id] = [x for x in new_lots if x is not t]
        events = [ExecEvent("filled", bar.ts_open, s, new_lots[0], orders=self._snap(*snaps))]
        for t in new_lots:
            if t in self.lots:
                ev = self._manage(t, bar, entry_bar=True)
                if ev:
                    events.append(ev)
        return events

    def _move_siblings_to_breakeven(self, t: Trade, ts: datetime) -> tuple:
        out = []
        for sib in self._siblings.get(t.trade_id, []):
            if sib in self.lots:
                sib.stop_price = sib.entry_price
                o = self._working.get(self._lot_group[sib.trade_id], {}).get("stop_loss")
                if o is not None:
                    o.price = sib.entry_price
                    o.note = "stop movido para o zero a zero (alvo do scalp atingido)"
                    out.append(o)
        return self._snap(*out)

    def _manage(self, t: Trade, bar: Bar, entry_bar: bool) -> ExecEvent | None:
        sign = t.side.sign
        slip_out = self.spec.ticks(self.costs.exit_slippage_ticks + self.costs.spread_ticks)
        slip_tgt = self.spec.ticks(self.costs.target_slippage_ticks)
        t.bars_held += 1
        if entry_bar:
            trig = bar.open if (t.entry_gap or t.signal.order_type == "market") else t.signal.entry_price
            hit, fav, adv = entry_bar_path(bar, sign, trig, t.stop_price, t.target_price)
            stop_hit, tgt_hit = hit == "stop", hit == "target"
        else:
            fav = (bar.high - t.entry_price) if sign > 0 else (t.entry_price - bar.low)
            adv = (t.entry_price - bar.low) if sign > 0 else (bar.high - t.entry_price)
            stop_hit = bar.low <= t.stop_price if sign > 0 else bar.high >= t.stop_price
            tgt_hit = bar.high >= t.target_price if sign > 0 else bar.low <= t.target_price
        t.mfe_points = max(t.mfe_points, fav)
        t.mae_points = max(t.mae_points, adv)
        px = reason = None
        if not entry_bar:
            if (bar.open <= t.stop_price) if sign > 0 else (bar.open >= t.stop_price):
                px, reason = bar.open - sign * slip_out, "stop_gap"
            elif (bar.open >= t.target_price) if sign > 0 else (bar.open <= t.target_price):
                px, reason = t.target_price - sign * slip_tgt, "target_gap"
        if reason is None:
            if stop_hit:
                px, reason = t.stop_price - sign * slip_out, ("breakeven" if t.stop_price == t.entry_price else "stop")
                t.ambiguous_bar = tgt_hit
            elif tgt_hit:
                px, reason = t.target_price - sign * slip_tgt, "target"
            elif t.signal.time_exit_bars and t.bars_held >= t.signal.time_exit_bars:
                px, reason = bar.close - sign * slip_out, "time_exit"
            elif self._flatten_time(bar):
                px, reason = bar.close - sign * slip_out, "session_end"
        if reason is None:
            return None
        px = self.spec.round_to_tick(px, "down" if sign > 0 else "up")
        return self._exit(t, bar.ts_close, px, reason)

    def _exit(self, t: Trade, ts: datetime, px: float, reason: str) -> ExecEvent:
        g = self._lot_group[t.trade_id]
        t.exit_ts, t.exit_price, t.exit_reason = ts, px, reason
        t.points = round((px - t.entry_price) * t.side.sign, 10)
        q = t.qty
        t.gross_brl = self.spec.pnl_money(t.points, q, px)
        t.costs_brl = self.costs.round_trip_fees(q)
        t.net_brl = round(t.gross_brl - t.costs_brl, 2)
        risk = abs(t.entry_price - t.stop_price) or t.signal.risk_points
        t.r_multiple = round(t.points / risk, 4) if risk else None
        risk_brl = self.spec.pnl_money(risk, q, px)
        t.net_r = round(t.net_brl / risk_brl, 4) if risk_brl else None
        self.lots.remove(t)
        self.closed.append(t)
        self.realized_brl += t.net_brl
        if reason in ("stop", "stop_gap", "breakeven"):
            orders = self._snap(self._finish(g, "stop_loss", "filled", ts, px), self._finish(g, "take_profit", "cancelled", ts, note="OCO: stop executado"))
        elif reason in ("target", "target_gap"):
            orders = self._snap(self._finish(g, "take_profit", "filled", ts, px), self._finish(g, "stop_loss", "cancelled", ts, note="OCO: alvo executado"))
            if t.signal.breakeven_after_first:
                orders = orders + self._move_siblings_to_breakeven(t, ts)
        else:
            why = EXIT_REASON_PT.get(reason, reason)
            a = self._finish(g, "stop_loss", "cancelled", ts, note=f"saída por {why}")
            b = self._finish(g, "take_profit", "cancelled", ts, note=f"saída por {why}")
            m = self._order(g, "market_exit", "sell" if t.side is Side.LONG else "buy", "market", None, ts, t.signal.setup_label,
                            f"saída por {why}", qty=t.qty)
            self._finish(g, "market_exit", "filled", ts, px)
            orders = self._snap(a, b, m)
        return ExecEvent("closed", ts, t.signal, t, reason, orders=orders)
