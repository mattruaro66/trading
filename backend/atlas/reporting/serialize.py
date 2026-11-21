"""Serialização para API/relatórios (JSON)."""
from __future__ import annotations

import math

from atlas.core.types import Bar
from atlas.pipeline import PipelineOutput


def bar_dict(b: Bar) -> dict:
    return {"symbol": b.symbol.value, "contract": b.contract, "ts_open": b.ts_open.isoformat(),
            "time": int(b.ts_open.timestamp()), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
            "volume": b.volume, "trades": b.trades, "status": b.status.value, "quality": b.quality.value,
            "quality_flags": list(b.quality_flags), "source": b.source, "synthetic": b.synthetic}


def output_dict(out: PipelineOutput) -> dict:
    s = out.snapshot
    marks = []
    for legs in (s.bull_legs, s.bear_legs):
        if legs.event:
            marks.append({"label": legs.event, "side": legs.side.value})
    return {
        "bar": bar_dict(out.bar),
        "metrics": s.metrics.as_dict(),
        "regime": s.regime.as_dict(),
        "legs": {"bull": {"in_pullback": s.bull_legs.in_pullback, "count": s.bull_legs.count, "event": s.bull_legs.event},
                 "bear": {"in_pullback": s.bear_legs.in_pullback, "count": s.bear_legs.count, "event": s.bear_legs.event}},
        "marks": marks,
        "data_ok": s.data_ok,
        "reset_reason": s.reset_reason,
        "evaluations": [{"side": e.side.value, "setup": e.setup_label, "stage": e.stage, "passed": e.passed,
                         "criteria": [c.as_dict() for c in e.criteria]} for e in out.evaluations],
        "signals": [{"side": x.side.value, "setup": x.setup_label, "entry": x.entry_price, "stop": x.stop_price,
                     "target": x.target_price} for x in out.signals],
        "exec_events": [{"kind": e.kind, "ts": e.ts.isoformat(), "reason": e.reason,
                         "trade": e.trade.as_dict() if e.trade else None, "orders": list(e.orders)}
                        for e in out.exec_events],
    }


def json_safe(obj):
    """Converte inf/nan em None recursivamente (JSON estrito)."""
    if isinstance(obj, float):
        return None if (math.isinf(obj) or math.isnan(obj)) else obj
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):  # numpy scalars
        return json_safe(obj.item())
    return obj
