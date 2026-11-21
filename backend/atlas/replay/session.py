"""Historical Replay Engine.

A sessão mantém todos os candles no servidor e expõe apenas os já
"revelados" (``cursor``). Cada passo alimenta o MESMO ``AnalysisPipeline`` e
``AlertEngine`` usados pelo backtest e pelo live. Não há API para ler candles
além do cursor.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from atlas.alerts.engine import AlertConfig, AlertEngine
from atlas.backtest.costs import CostModel
from atlas.core.types import Bar, Symbol
from atlas.explanation.engine import explain_alert
from atlas.pipeline import AnalysisPipeline
from atlas.reporting.serialize import bar_dict, output_dict
from atlas.strategy.h2l2_momentum import H2L2MomentumParams
from atlas.strategy.second_entry import SecondEntryParams


@dataclass
class ReplaySession:
    symbol: Symbol
    bars: list[Bar]
    params: SecondEntryParams | H2L2MomentumParams
    costs: CostModel = field(default_factory=CostModel)
    evidence: dict | None = None
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    start_index: int = 0

    def __post_init__(self) -> None:
        self.bars = sorted(self.bars, key=lambda b: b.ts_open)
        self.pipeline = AnalysisPipeline(self.symbol, self.params, costs=self.costs)
        cfg = AlertConfig(cooldown_bars=0) if isinstance(self.params, H2L2MomentumParams) else AlertConfig()
        self.alerts = AlertEngine(cfg, explain=explain_alert,
                                  evidence_provider=(lambda s: self.evidence) if self.evidence else None)
        self.cursor = 0  # quantidade de candles já revelados
        self.log: list[dict] = []
        # avanço silencioso até start_index (aquecimento) — mesmas regras, sem omitir candles
        while self.cursor < min(self.start_index, len(self.bars)):
            self._advance()

    @property
    def total(self) -> int:
        return len(self.bars)

    @property
    def finished(self) -> bool:
        return self.cursor >= len(self.bars)

    def _advance(self) -> dict:
        bar = self.bars[self.cursor]
        out = self.pipeline.on_bar_closed(bar)
        events = self.alerts.process(out)
        self.cursor += 1
        d = output_dict(out)
        d["alert_events"] = [e.as_dict() for e in events]
        d["index"] = self.cursor - 1
        d["position"] = self.pipeline.sim.position(bar)
        for e in events:
            self.log.append({"ts": e.ts.isoformat(), "state": e.state.value, "reason": e.reason, "alert_id": e.alert_id})
        return d

    def step(self, n: int = 1) -> list[dict]:
        out = []
        for _ in range(max(0, n)):
            if self.finished:
                break
            out.append(self._advance())
        return out

    def revealed_bars(self) -> list[dict]:
        return [bar_dict(b) for b in self.bars[: self.cursor]]

    def state(self) -> dict:
        return {"session_id": self.session_id, "symbol": self.symbol.value, "cursor": self.cursor, "total": self.total,
                "finished": self.finished,
                "current_ts": self.bars[self.cursor - 1].ts_open.isoformat() if self.cursor else None,
                "synthetic": any(b.synthetic for b in self.bars),
                "closed_trades": [t.as_dict() for t in self.pipeline.sim.closed],
                "orders": [o.as_dict() for o in self.pipeline.sim.orders],
                "position": self.pipeline.sim.position(),
                "alerts": [a.as_dict() for a in self.alerts.list("confirmed_setup")]}
