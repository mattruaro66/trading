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
    qty: float = 1.0  # contratos (futuros) ou lotes (forex) deste lote
    initial_risk: float | None = None  # distância entrada–stop na execução (o stop pode andar depois)

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
            "lot": getattr(self, "lot", "único"),
            "qty": self.qty,
        }


ROLE_PT = {"entry": "entrada", "stop_loss": "stop", "take_profit": "alvo", "market_exit": "saída a mercado"}
STATUS_PT = {"working": "pendente", "filled": "executada", "cancelled": "cancelada", "expired": "expirada"}
TYPE_PT = {"stop": "stop", "limit": "limitada", "market": "a mercado"}
EXIT_REASON_PT = {"breakeven": "zero a zero", "trailing": "stop móvel", "bar_exit": "barra contra (saída no fechamento)", "stop": "stop", "stop_gap": "stop (gap)", "target": "alvo", "target_gap": "alvo (gap)",
                  "time_exit": "tempo", "session_end": "fim da sessão", "data_gap_exit": "lacuna de dados"}


@dataclass
class SimOrder:
    """Ordem SIMULADA. Nunca é enviada a corretora/bolsa: existe apenas dentro do simulador.

    Fluxo do método (Estratégia A): ordem stop de entrada válida por 1 candle → ao executar, stop e alvo
    entram como par OCO → a execução de uma perna cancela a outra; saídas por tempo, sessão ou lacuna
    são ordens a mercado que cancelam o par OCO.
    """

    order_id: int
    trade_group: int  # agrupa as ordens de uma mesma configuração
    role: str  # entry | stop_loss | take_profit | market_exit
    action: str  # buy | sell
    type: str  # stop | limit | market
    qty: int
    price: float | None
    created_ts: datetime
    status: str = "working"  # working | filled | cancelled | expired
    done_ts: datetime | None = None
    fill_price: float | None = None
    note: str = ""
    setup: str = ""

    def as_dict(self) -> dict:
        return {"order_id": self.order_id, "group": self.trade_group, "role": self.role, "role_pt": ROLE_PT[self.role],
                "action": self.action, "action_pt": "compra" if self.action == "buy" else "venda", "type": self.type,
                "type_pt": TYPE_PT[self.type], "qty": self.qty, "price": self.price, "status": self.status,
                "status_pt": STATUS_PT[self.status], "created_ts": self.created_ts.isoformat(),
                "done_ts": self.done_ts.isoformat() if self.done_ts else None, "fill_price": self.fill_price,
                "note": self.note, "setup": self.setup, "simulated": True}


@dataclass(frozen=True)
class ExecEvent:
    kind: str  # order_placed | order_expired | order_cancelled | filled | closed | signal_skipped
    ts: datetime
    signal: Signal
    trade: Trade | None = None
    reason: str | None = None
    orders: tuple = ()  # snapshots (dict) das ordens simuladas alteradas neste evento


@dataclass
class _Pending:
    signal: Signal
    placed_after: Bar
    qty: float = 1.0


def entry_bar_path(bar: Bar, sign: int, trigger: float, stop: float, target: float) -> tuple[str | None, float, float]:
    """Candle de entrada: stop/alvo só contam DEPOIS da ativação da ordem.

    Caminho intrabar (convenção do testador do MetaTrader): candle de alta O→L→H→C; de baixa O→H→L→C;
    doji: primeiro o extremo mais próximo da abertura. Retorna ("stop" | "target" | None, MFE, MAE) medidos
    só a partir da ativação. Sem dados de 1 minuto a ordem real dentro do candle é desconhecida.
    """
    o, h, l, c = bar.open, bar.high, bar.low, bar.close
    if c > o or (c == o and (o - l) <= (h - o)):
        pts = [o, l, h, c]
    else:
        pts = [o, h, l, c]
    entered = (o >= trigger) if sign > 0 else (o <= trigger)
    best = worst = 0.0
    for a, b in zip(pts, pts[1:]):
        if not entered:
            crosses = (a < trigger <= b) if sign > 0 else (a > trigger >= b)
            if not crosses:
                continue
            entered, a = True, trigger
        best = max(best, sign * (b - trigger))
        worst = max(worst, sign * (trigger - b))
        if sign * (b - a) < 0 and sign * (b - stop) <= 0:
            return "stop", best, worst
        if sign * (b - a) > 0 and sign * (b - target) >= 0:
            return "target", best, worst
    return None, best, worst


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
        self._order_ids = itertools.count(1)
        self._groups = itertools.count(1)
        self.orders: list[SimOrder] = []
        self._working: dict[str, SimOrder] = {}  # role → ordem pendente do grupo atual
        self._group: int | None = None
        self.realized_brl = 0.0
        self._last_qty = self.costs.contracts

    # ---------------- livro de ordens simulado ----------------
    def _new_order(self, role: str, action: str, otype: str, price: float | None, ts: datetime, setup: str,
                   note: str = "", qty: float | None = None) -> SimOrder:
        q = qty if qty is not None else (self.pending.qty if self.pending is not None else
                                         (self.open_trade.qty if self.open_trade else self.costs.contracts))
        o = SimOrder(next(self._order_ids), self._group or 0, role, action, otype, q, price, ts,
                     note=note, setup=setup)
        self.orders.append(o)
        if o.status == "working":
            self._working[role] = o
        return o

    def _finish(self, role: str, status: str, ts: datetime, fill: float | None = None, note: str = "") -> SimOrder | None:
        o = self._working.pop(role, None)
        if o is None:
            return None
        o.status, o.done_ts, o.fill_price = status, ts, fill
        if note:
            o.note = note
        return o

    @staticmethod
    def _snap(*orders) -> tuple:
        return tuple(o.as_dict() for o in orders if o is not None)

    def position(self, last: Bar | None = None) -> dict:
        """Situação atual da conta simulada (sem ordens reais)."""
        bar = last or self.prev_bar
        base = {"realized_brl": round(self.realized_brl, 2), "closed_trades": len(self.closed),
                "working_orders": [o.as_dict() for o in self._working.values()]}
        t = self.open_trade
        if t is None:
            return {**base, "state": "pending_entry" if self.pending else "flat"}
        mark = bar.close if bar else t.entry_price
        pts = round((mark - t.entry_price) * t.side.sign, 10)
        return {**base, "state": "long" if t.side is Side.LONG else "short", "qty": t.qty,
                "entry_price": t.entry_price, "stop_price": t.stop_price, "target_price": t.target_price,
                "mark_price": mark, "unrealized_points": pts, "unrealized_brl": self.spec.pnl_money(pts, t.qty, mark),
                "bars_held": t.bars_held, "setup": t.signal.setup_label}

    # ------------------------------------------------------------------
    def submit(self, signal: Signal, signal_bar: Bar) -> ExecEvent:
        if self.open_trade is not None or self.pending is not None:
            return ExecEvent("signal_skipped", signal_bar.ts_close, signal, reason="position_or_order_active")
        qty = self.costs.quantity(self.spec, signal.risk_points, signal.entry_price)
        if qty <= 0:
            return ExecEvent("signal_skipped", signal_bar.ts_close, signal, reason="risk_exceeds_budget_at_min_size")
        self.pending = _Pending(signal, signal_bar, qty)
        self._group = next(self._groups)
        action = "buy" if signal.side is Side.LONG else "sell"
        o = self._new_order("entry", action, "stop", signal.entry_price, signal_bar.ts_close, signal.setup_label,
                            "válida somente no próximo candle de 5 min")
        return ExecEvent("order_placed", signal_bar.ts_close, signal, orders=self._snap(o))

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
            px = self.spec.round_to_tick(px, "down" if t.side is Side.LONG else "up")
            self._close(t, prev.ts_close, px, "data_gap_exit")
            events.append(ExecEvent("closed", prev.ts_close, t.signal, t, "data_gap_exit",
                                    orders=self._exit_orders(t, prev.ts_close, "data_gap_exit")))

        if self.pending is not None:
            pend = self.pending
            self._last_qty = pend.qty
            self.pending = None
            if not contiguous or pend.placed_after is not prev:
                o = self._finish("entry", "cancelled", bar.ts_open, note="candle seguinte ausente/não contíguo")
                events.append(ExecEvent("order_cancelled", bar.ts_open, pend.signal, reason="next_bar_not_contiguous",
                                        orders=self._snap(o)))
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
                return self._expire(s, bar)
        else:
            if bar.open <= s.entry_price:
                fill, gap = bar.open - slip_in, True
            elif bar.low <= s.entry_price:
                fill = s.entry_price - slip_in
            else:
                return self._expire(s, bar)
        fill = self.spec.round_to_tick(fill, "up" if sign > 0 else "down")
        t = Trade(next(self._ids), s, s.side, bar.ts_open, fill, s.stop_price, s.target_price, entry_gap=gap,
                  qty=self._last_qty)
        self.open_trade = t
        entry = self._finish("entry", "filled", bar.ts_open, fill, "gap de abertura" if gap else "")
        exit_action = "sell" if s.side is Side.LONG else "buy"
        sl = self._new_order("stop_loss", exit_action, "stop", s.stop_price, bar.ts_open, s.setup_label, "OCO com o alvo")
        tp = self._new_order("take_profit", exit_action, "limit", s.target_price, bar.ts_open, s.setup_label, "OCO com o stop")
        events = [ExecEvent("filled", bar.ts_open, s, t, orders=self._snap(entry, sl, tp))]
        events.extend(self._manage(t, bar, entry_bar=True))
        return events

    def _expire(self, s: Signal, bar: Bar) -> list[ExecEvent]:
        o = self._finish("entry", "expired", bar.ts_close, note="preço não atingido no candle seguinte")
        return [ExecEvent("order_expired", bar.ts_close, s, reason="not_triggered", orders=self._snap(o))]

    def _exit_orders(self, t: Trade, ts: datetime, reason: str) -> tuple:
        if reason in ("stop", "stop_gap"):
            a = self._finish("stop_loss", "filled", ts, t.exit_price, "gap além do stop" if reason == "stop_gap" else "")
            b = self._finish("take_profit", "cancelled", ts, note="OCO: stop executado")
            return self._snap(a, b)
        if reason in ("target", "target_gap"):
            a = self._finish("take_profit", "filled", ts, t.exit_price, "gap além do alvo" if reason == "target_gap" else "")
            b = self._finish("stop_loss", "cancelled", ts, note="OCO: alvo executado")
            return self._snap(a, b)
        why = EXIT_REASON_PT.get(reason, reason)
        a = self._finish("stop_loss", "cancelled", ts, note=f"saída por {why}")
        b = self._finish("take_profit", "cancelled", ts, note=f"saída por {why}")
        action = "sell" if t.side is Side.LONG else "buy"
        m = self._new_order("market_exit", action, "market", None, ts, t.signal.setup_label, f"saída por {why}", qty=t.qty)
        self._finish("market_exit", "filled", ts, t.exit_price)
        return self._snap(a, b, m)

    def _manage(self, t: Trade, bar: Bar, entry_bar: bool) -> list[ExecEvent]:
        sign = t.side.sign
        slip_out = self._slip(self.costs.exit_slippage_ticks + self.costs.spread_ticks)
        slip_tgt = self._slip(self.costs.target_slippage_ticks)
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
        return [ExecEvent("closed", bar.ts_close, t.signal, t, reason, orders=self._exit_orders(t, bar.ts_close, reason))]

    def _close(self, t: Trade, ts: datetime, px: float, reason: str) -> None:
        t.exit_ts, t.exit_price, t.exit_reason = ts, px, reason
        t.points = round((px - t.entry_price) * t.side.sign, 10)
        q = t.qty
        t.gross_brl = self.spec.pnl_money(t.points, q, px)
        t.costs_brl = self.costs.round_trip_fees(q)
        t.net_brl = round(t.gross_brl - t.costs_brl, 2)
        risk_brl = self.spec.pnl_money(t.signal.risk_points, q, px)
        t.r_multiple = round(t.points / t.signal.risk_points, 4) if t.signal.risk_points else None
        t.net_r = round(t.net_brl / risk_brl, 4) if risk_brl else None
        self.closed.append(t)
        self.open_trade = None
        self.realized_brl += t.net_brl
