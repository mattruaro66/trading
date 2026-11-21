"""Alert Engine — estados, deduplicação, cooldown, bloqueio por dados e registro imutável.

Estados: observation → preparation → confirmed → (invalidated | closed).

Imutabilidade: o conteúdo de um alerta (``Alert.payload``) é congelado na
criação e recebe ``content_hash`` SHA-256. Mudanças de estado são ``AlertEvent``
*append-only*, encadeados por ``prev_hash`` (cadeia verificável). Nada que
aconteça depois altera o conteúdo original.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from atlas.backtest.execution import ExecEvent
from atlas.core.timeframe import TIMEFRAME_LABEL
from atlas.pipeline import PipelineOutput
from atlas.strategy.base import SetupEvaluation, Signal


class AlertState(StrEnum):
    OBSERVATION = "observation"
    PREPARATION = "preparation"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    CLOSED = "closed"


STATE_LABEL_PT = {
    AlertState.OBSERVATION: "Observação",
    AlertState.PREPARATION: "Preparação",
    AlertState.CONFIRMED: "Configuração confirmada",
    AlertState.INVALIDATED: "Invalidação",
    AlertState.CLOSED: "Encerramento hipotético",
}


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class AlertEvent:
    alert_id: str
    seq: int
    state: AlertState
    ts: datetime
    reason: str
    data: dict
    prev_hash: str
    event_hash: str

    def as_dict(self) -> dict:
        return {"alert_id": self.alert_id, "seq": self.seq, "state": self.state.value,
                "state_label": STATE_LABEL_PT[self.state], "ts": self.ts.isoformat(), "reason": self.reason,
                "data": self.data, "prev_hash": self.prev_hash, "event_hash": self.event_hash}


@dataclass
class Alert:
    alert_id: str
    setup_key: str
    created_ts: datetime
    payload: dict  # imutável após criação (cópia profunda serializada)
    content_hash: str
    events: list[AlertEvent] = field(default_factory=list)

    @property
    def state(self) -> AlertState:
        return self.events[-1].state if self.events else AlertState.OBSERVATION

    def verify(self) -> bool:
        if _hash(self.payload) != self.content_hash:
            return False
        prev = self.content_hash
        for e in self.events:
            body = {"alert_id": e.alert_id, "seq": e.seq, "state": e.state.value, "ts": e.ts.isoformat(),
                    "reason": e.reason, "data": e.data, "prev_hash": prev}
            if e.prev_hash != prev or _hash(body) != e.event_hash:
                return False
            prev = e.event_hash
        return True

    def as_dict(self) -> dict:
        return {"alert_id": self.alert_id, "setup_key": self.setup_key, "created_ts": self.created_ts.isoformat(),
                "state": self.state.value, "state_label": STATE_LABEL_PT[self.state], "payload": self.payload,
                "content_hash": self.content_hash, "events": [e.as_dict() for e in self.events],
                "integrity_ok": self.verify()}


@dataclass
class AlertConfig:
    cooldown_bars: int = 3
    emit_observation: bool = True
    emit_preparation: bool = True


class AlertEngine:
    """Consome ``PipelineOutput`` (e status de qualidade dos dados) e mantém o registro de alertas."""

    def __init__(self, config: AlertConfig | None = None, explain=None, evidence_provider=None):
        self.config = config or AlertConfig()
        self.alerts: dict[str, Alert] = {}
        self.by_key: dict[str, str] = {}
        self.suppressed: list[dict] = []
        self._last_confirmed_bar: dict[tuple[str, str], int] = {}
        self._bar_index = 0
        self._explain = explain  # callable(signal, evaluation, evidence) -> dict
        self._evidence = evidence_provider  # callable(signal) -> dict | None
        self.data_ok = True
        self.data_status_reason: str | None = None
        self.new_events: list[AlertEvent] = []
        self._signal_alert: dict[int, str] = {}

    # ------------------------------------------------------------------
    def set_data_status(self, ok: bool, reason: str | None = None) -> None:
        self.data_ok = ok
        self.data_status_reason = reason

    def _append(self, alert: Alert, state: AlertState, ts: datetime, reason: str, data: dict | None = None) -> AlertEvent:
        prev = alert.events[-1].event_hash if alert.events else alert.content_hash
        seq = len(alert.events)
        data = json.loads(json.dumps(data or {}, default=str))
        body = {"alert_id": alert.alert_id, "seq": seq, "state": state.value, "ts": ts.isoformat(),
                "reason": reason, "data": data, "prev_hash": prev}
        ev = AlertEvent(alert.alert_id, seq, state, ts, reason, data, prev, _hash(body))
        alert.events.append(ev)
        self.new_events.append(ev)
        return ev

    def _create(self, key: str, ts: datetime, payload: dict) -> Alert:
        frozen = json.loads(json.dumps(payload, default=str, ensure_ascii=False))
        a = Alert(str(uuid.uuid4()), key, ts, frozen, _hash(frozen))
        self.alerts[a.alert_id] = a
        self.by_key[key] = a.alert_id
        return a

    @staticmethod
    def _key(e: SetupEvaluation, symbol: str, contract: str | None) -> str:
        pb = e.pullback_start_ts.isoformat() if e.pullback_start_ts else "na"
        return f"{e.strategy_id}:{symbol}:{contract}:{e.side.value}:{pb}"

    def _base_payload(self, e: SetupEvaluation, out: PipelineOutput) -> dict:
        b = out.bar
        return {
            "symbol": b.symbol.value, "contract": b.contract, "timeframe": TIMEFRAME_LABEL,
            "strategy_id": e.strategy_id, "side": e.side.value, "setup": e.setup_label,
            "bar_ts_open": b.ts_open.isoformat(), "bar_ts_close": b.ts_close.isoformat(),
            "regime": out.snapshot.regime.as_dict(), "engine_version": out.snapshot.engine_version,
            "synthetic_data": b.synthetic, "data_source": b.source,
        }

    # ------------------------------------------------------------------
    def process(self, out: PipelineOutput) -> list[AlertEvent]:
        self.new_events = []
        self._bar_index += 1
        b = out.bar
        sym, contract = b.symbol.value, b.contract

        # 1) eventos de execução das ordens/posições de alertas confirmados
        for ev in out.exec_events:
            self._handle_exec(ev)

        # 2) avaliações da estratégia
        if not self.data_ok or not out.snapshot.data_ok:
            if any(e.stage == "candidate" and e.passed for e in out.evaluations):
                self.suppressed.append({"ts": b.ts_close.isoformat(), "reason": self.data_status_reason or "data_quality",
                                        "symbol": sym})
            return self.new_events

        for e in out.evaluations:
            key = self._key(e, sym, contract)
            existing = self.alerts.get(self.by_key.get(key, ""))
            if e.stage == "observation" and self.config.emit_observation and existing is None:
                a = self._create(key, b.ts_close, {**self._base_payload(e, out), "kind": "setup_tracking"})
                self._append(a, AlertState.OBSERVATION, b.ts_close, "Pullback iniciado em tendência a favor.")
            elif e.stage == "preparation" and self.config.emit_preparation:
                if existing is None:
                    existing = self._create(key, b.ts_close, {**self._base_payload(e, out), "kind": "setup_tracking"})
                if existing.state in (AlertState.OBSERVATION,) or not existing.events:
                    self._append(existing, AlertState.PREPARATION, b.ts_close,
                                 f"Primeira tentativa (ex.: H1/L1) ocorreu; aguardando a segunda entrada ({e.setup_label}).")
            elif e.stage == "candidate" and e.passed and e.signal is not None:
                self._confirm(e, out, key, existing)
        return self.new_events

    def _confirm(self, e: SetupEvaluation, out: PipelineOutput, key: str, existing: Alert | None) -> None:
        s: Signal = e.signal
        b = out.bar
        ck = (b.symbol.value, s.side.value)
        last = self._last_confirmed_bar.get(ck)
        if last is not None and self._bar_index - last <= self.config.cooldown_bars:
            self.suppressed.append({"ts": b.ts_close.isoformat(), "reason": "cooldown", "symbol": b.symbol.value})
            return
        conf_key = f"{key}:confirmed:{b.ts_open.isoformat()}"
        if conf_key in self.by_key:  # deduplicação
            return
        evidence = self._evidence(s) if self._evidence else None
        payload = {
            **self._base_payload(e, out), "kind": "confirmed_setup",
            "signal": {"entry_price": s.entry_price, "stop_price": s.stop_price, "target_price": s.target_price,
                       "risk_points": s.risk_points, "risk_ticks": s.risk_ticks, "reward_risk": round(s.reward_risk, 3),
                       "target_r": s.target_r, "time_exit_bars": s.time_exit_bars, "valid_for": "próximo candle de 5 min",
                       "strategy_version": s.strategy_version, "params_hash": s.params_hash, "profile": s.profile},
            "criteria": [c.as_dict() for c in s.criteria],
            "signal_bar": {"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume},
            "context": s.context,
            "evidence": evidence,
            "macro": {"status": "não integrado", "note": "Calendário macroeconômico não integrado no MVP (Fase 4); eventos não verificados."},
            "tracking_alert_id": existing.alert_id if existing else None,
        }
        if self._explain:
            payload["explanation"] = self._explain(payload)
        a = self._create(conf_key, b.ts_close, payload)
        self._append(a, AlertState.CONFIRMED, b.ts_close, f"{s.setup_label} confirmado no fechamento da barra de sinal.")
        self._last_confirmed_bar[ck] = self._bar_index
        self._signal_alert[id(s)] = a.alert_id

    def _handle_exec(self, ev: ExecEvent) -> None:
        aid = self._signal_alert.get(id(ev.signal))
        if not aid:
            return
        a = self.alerts[aid]
        if ev.kind in ("order_expired", "order_cancelled", "signal_skipped", "closed"):
            self._signal_alert.pop(id(ev.signal), None)  # estado terminal: libera o vínculo
        if ev.kind in ("order_expired", "order_cancelled"):
            self._append(a, AlertState.INVALIDATED, ev.ts, {
                "not_triggered": "Ordem hipotética não acionada no candle seguinte.",
                "next_bar_not_contiguous": "Candle seguinte ausente/não contíguo; configuração cancelada.",
            }.get(ev.reason or "", ev.reason or ev.kind))
        elif ev.kind == "signal_skipped":
            self._append(a, AlertState.INVALIDATED, ev.ts, "Simulação já possuía posição/ordem ativa; sinal não simulado.")
        elif ev.kind == "filled" and ev.trade:
            # registro informativo de entrada (estado permanece "confirmado")
            self._append(a, AlertState.CONFIRMED, ev.ts, "Entrada hipotética acionada.",
                         {"entry_price": ev.trade.entry_price, "entry_gap": ev.trade.entry_gap})
        elif ev.kind == "closed" and ev.trade:
            t = ev.trade
            self._append(a, AlertState.CLOSED, ev.ts, f"Encerramento hipotético: {t.exit_reason}.",
                         {"exit_price": t.exit_price, "exit_reason": t.exit_reason, "points": t.points,
                          "net_brl": t.net_brl, "r_multiple": t.r_multiple, "ambiguous_bar": t.ambiguous_bar})

    def list(self, kind: str | None = None) -> list[Alert]:
        al = sorted(self.alerts.values(), key=lambda a: a.created_ts)
        if kind:
            al = [a for a in al if a.payload.get("kind") == kind]
        return al
