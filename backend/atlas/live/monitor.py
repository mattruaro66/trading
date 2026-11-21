"""LiveMonitor — orquestra provedor → Candle Engine → Price Action/Estratégia → Alertas.

Fluxo de falha (LIVE_DATA_ARCHITECTURE.md §5):
1. Conexão marcada como degradada (erro do provedor ou heartbeat vencido).
2. Alertas suspensos (``AlertEngine.set_data_status(False)``).
3. Reconexão com backoff exponencial (+ jitter semeado).
4. Recuperação dos eventos ausentes, se o provedor suportar.
5. Reconstrução dos candles afetados.
6. Validação: candles não recuperados permanecem inválidos; o pipeline reinicia na lacuna.
7. Alertas retomados somente após recuperação completa E um candle limpo fechado.
"""
from __future__ import annotations

import asyncio
import random
import statistics
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from atlas.alerts.engine import AlertConfig, AlertEngine
from atlas.core.logging import get_logger, log_event
from atlas.core.timeframe import TZ
from atlas.core.types import Quality, Symbol
from atlas.explanation.engine import explain_alert
from atlas.live.candle_engine import CandleEngine
from atlas.live.models import (
    CandleClosed, CandleInvalidated, CandleUpdated, ConnectionDegraded, ConnectionRecovered, ConnectionState,
    DataGapDetected, MarketEvent,
)
from atlas.live.provider import AuthenticationError, MarketDataProvider
from atlas.pipeline import AnalysisPipeline
from atlas.reporting.serialize import bar_dict, output_dict
from atlas.strategy.second_entry import SecondEntryParams

log = get_logger("atlas.live")


@dataclass
class ReconnectPolicy:
    base_delay: float = 1.0
    max_delay: float = 60.0
    max_attempts: int = 10
    jitter: float = 0.2
    seed: int = 1

    def delays(self) -> list[float]:
        rng = random.Random(self.seed)
        out = []
        for i in range(self.max_attempts):
            d = min(self.max_delay, self.base_delay * (2 ** i))
            out.append(d * (1 + rng.uniform(-self.jitter, self.jitter)))
        return out


Publisher = Callable[[dict], Awaitable[None] | None]


class LiveMonitor:
    def __init__(self, provider: MarketDataProvider, symbol: Symbol | str, contract: str, *,
                 params: SecondEntryParams | None = None, publish: Publisher | None = None, repo=None,
                 heartbeat_timeout: timedelta = timedelta(seconds=30), reconnect: ReconnectPolicy | None = None,
                 clock: Callable[[], datetime] | None = None, sleep: Callable[[float], Awaitable[None]] | None = None,
                 evidence: dict | None = None):
        self.provider = provider
        self.symbol = Symbol(symbol)
        self.contract = contract
        self.publish_cb = publish
        self.repo = repo
        self.heartbeat_timeout = heartbeat_timeout
        self.reconnect_policy = reconnect or ReconnectPolicy()
        self._clock = clock or (lambda: datetime.now(TZ))
        self._sleep = sleep or asyncio.sleep
        self.engine = CandleEngine(self.symbol, contract, source=f"{provider.name}{':SIMULATED' if provider.simulated else ''}")
        self.pipeline = AnalysisPipeline(self.symbol, params)
        self.alerts = AlertEngine(AlertConfig(), explain=explain_alert,
                                  evidence_provider=(lambda s: evidence) if evidence else None)
        self.state = ConnectionState.DISCONNECTED
        self.events_log: deque[dict] = deque(maxlen=1000)
        self.forming: dict | None = None
        self._latencies: deque[float] = deque(maxlen=500)
        self._last_event_wall: datetime | None = None
        self._last_exchange_ts: datetime | None = None
        self._degraded_at: datetime | None = None
        self._awaiting_clean_candle = False
        self.reconnect_attempts = 0
        self._reconnecting = False
        provider.on_trade(self._on_trade)
        provider.on_quote(self._on_quote)
        provider.on_error(self._on_error)

    # ------------------------------------------------------------------
    async def _publish(self, msg: dict) -> None:
        self.events_log.append(msg)
        if self.publish_cb:
            r = self.publish_cb(msg)
            if hasattr(r, "__await__"):
                await r

    def _record(self, kind: str, ts: datetime, **details) -> None:
        log_event(log, kind, provider=self.provider.name, contract=self.contract, ts=ts.isoformat(), **details)
        if self.repo:
            try:
                self.repo.save_connection_event(self.provider.name, kind, ts, details)
            except Exception:  # persistência não pode derrubar o monitor
                log.exception("falha ao persistir evento de conexão")

    async def start(self) -> None:
        self.state = ConnectionState.CONNECTING
        try:
            await self.provider.connect()
        except AuthenticationError as e:
            self.state = ConnectionState.FAILED
            self._record("auth_failed", self._clock(), error=str(e))
            await self._publish({"type": "status", "status": self.status()})
            raise
        await self.provider.subscribe(self.contract)
        self.state = ConnectionState.CONNECTED
        self._record("connected", self._clock())
        await self._publish({"type": "status", "status": self.status()})

    async def stop(self) -> None:
        await self.provider.disconnect()
        self.state = ConnectionState.DISCONNECTED
        self._record("disconnected", self._clock())

    # ------------------------------------------------------------------
    async def _on_quote(self, ev: MarketEvent) -> None:
        # Cotações não constroem candles; apenas atualizam a latência observada.
        self._touch(ev)

    def _touch(self, ev: MarketEvent) -> None:
        self._latencies.append(ev.latency_ms)
        self._last_event_wall = self._clock()
        self._last_exchange_ts = ev.exchange_timestamp

    async def _on_trade(self, ev: MarketEvent) -> None:
        self._touch(ev)
        await self._handle(self.engine.ingest(ev))

    async def tick(self, now: datetime | None = None) -> None:
        """Chamado periodicamente (ou pelo relógio simulado): fecha candles e verifica heartbeat."""
        now = now or self._clock()
        await self._handle(self.engine.advance_clock(now))
        if (self.state is ConnectionState.CONNECTED and self._last_event_wall is not None
                and self._clock() - self._last_event_wall > self.heartbeat_timeout):
            await self._degrade("heartbeat_timeout")

    async def _on_error(self, exc: Exception) -> None:
        await self._degrade(f"provider_error: {exc}")
        await self.reconnect()

    async def _degrade(self, reason: str) -> None:
        if self.state is ConnectionState.DEGRADED:
            return
        ts = self._last_exchange_ts or self._clock()
        self.state = ConnectionState.DEGRADED
        self._degraded_at = ts
        self.engine.mark_degraded(ts)
        self.alerts.set_data_status(False, "connection_degraded")
        self._record("degraded", ts, reason=reason)
        ev = ConnectionDegraded(self.provider.name, ts, reason)
        await self._publish({"type": ev.kind, "ts": ts.isoformat(), "reason": reason, "status": self.status()})

    async def reconnect(self) -> bool:
        if self._reconnecting:
            return False
        self._reconnecting = True
        try:
            for delay in self.reconnect_policy.delays():
                self.reconnect_attempts += 1
                self.state = ConnectionState.RECOVERING
                await self._sleep(delay)
                try:
                    await self.provider.disconnect()
                    await self.provider.connect()
                except AuthenticationError as e:
                    self.state = ConnectionState.FAILED
                    self._record("auth_failed", self._clock(), error=str(e))
                    return False
                except Exception as e:  # tenta novamente com backoff
                    self._record("reconnect_failed", self._clock(), attempt=self.reconnect_attempts, error=str(e))
                    continue
                await self._recover()
                await self.provider.subscribe(self.contract)
                return True
            self.state = ConnectionState.FAILED
            self._record("reconnect_exhausted", self._clock(), attempts=self.reconnect_attempts)
            await self._publish({"type": "status", "status": self.status()})
            return False
        finally:
            self._reconnecting = False

    async def _recover(self) -> None:
        now = self._clock()
        recovered: list[MarketEvent] = []
        complete = False
        if self._degraded_at is not None and self.provider.supports_recovery:
            try:
                sess = self.engine.cal.session_for_ts(self.symbol, self._degraded_at)
                start = sess.bucket_open(self._degraded_at) if sess else self._degraded_at
                end = self._provider_now() or now
                recovered = await self.provider.recover_events(self.contract, start, end)
                self.engine.mark_recovered()
                await self._handle(self.engine.rebuild(recovered, start, end))
                complete = True
            except Exception as e:
                self._record("recovery_failed", now, error=str(e))
        if not complete:
            self.engine.mark_recovered()  # novos candles voltam a ser válidos; os afetados permanecem inválidos
        self.state = ConnectionState.CONNECTED
        self._degraded_at = None
        self._awaiting_clean_candle = True
        ev = ConnectionRecovered(self.provider.name, now, len(recovered), complete)
        self._record("recovered", now, recovered_events=len(recovered), complete=complete)
        await self._publish({"type": ev.kind, "ts": now.isoformat(), "recovered_events": len(recovered),
                             "complete": complete, "status": self.status()})

    def _provider_now(self) -> datetime | None:
        clock = getattr(self.provider, "clock", None)
        return clock() if callable(clock) else None

    # ------------------------------------------------------------------
    async def _handle(self, events) -> None:
        for ev in events:
            if isinstance(ev, CandleUpdated):
                self.forming = bar_dict(ev.bar)
                await self._publish({"type": ev.kind, "bar": self.forming})
            elif isinstance(ev, CandleClosed):
                await self._on_closed(ev)
            elif isinstance(ev, CandleInvalidated):
                self.alerts.set_data_status(False, f"candle_invalidated:{ev.reason}")
                self._awaiting_clean_candle = True
                await self._publish({"type": ev.kind, "bar": bar_dict(ev.bar), "reason": ev.reason})
            elif isinstance(ev, DataGapDetected):
                self._record("data_gap", ev.start, reason=ev.reason, **{k: str(v) for k, v in ev.details.items()})
                if ev.reason != "no_data_received":
                    self.alerts.set_data_status(False, f"data_gap:{ev.reason}")
                    self._awaiting_clean_candle = True
                await self._publish({"type": ev.kind, "start": ev.start.isoformat(),
                                     "end": ev.end.isoformat() if ev.end else None, "reason": ev.reason,
                                     "details": {k: str(v) for k, v in ev.details.items()}})

    async def _on_closed(self, ev: CandleClosed) -> None:
        bar = ev.bar
        if self.repo:
            try:
                self.repo.save_live_bar(bar)
            except Exception:
                log.exception("falha ao persistir candle")
        # retomada de alertas: conexão ok, recuperação concluída e candle limpo
        if (self._awaiting_clean_candle and bar.quality is Quality.OK and self.state is ConnectionState.CONNECTED
                and not self.engine.degraded):
            self._awaiting_clean_candle = False
            self.alerts.set_data_status(True)
        msg: dict = {"type": ev.kind, "bar": bar_dict(bar)}
        if self.pipeline.last_ts is None or bar.ts_open > self.pipeline.last_ts:
            out = self.pipeline.on_bar_closed(bar)
            alert_events = self.alerts.process(out)
            msg["analysis"] = output_dict(out)
            msg["alert_events"] = [e.as_dict() for e in alert_events]
            if self.repo:
                for aid in {e.alert_id for e in alert_events}:
                    try:
                        self.repo.save_alert(self.alerts.alerts[aid], "live" if not self.provider.simulated else "replay")
                    except Exception:
                        log.exception("falha ao persistir alerta")
        else:
            msg["note"] = "candle reconstruído anterior ao último candle analisado: persistido, não reanalisado"
        await self._publish(msg)

    # ------------------------------------------------------------------
    def status(self) -> dict:
        lat = list(self._latencies)
        p = self.provider.get_status().as_dict()
        p.update({
            "monitor_state": self.state.value, "symbol": self.symbol.value, "contract": self.contract,
            "last_event_wall": self._last_event_wall.isoformat() if self._last_event_wall else None,
            "last_exchange_ts": self._last_exchange_ts.isoformat() if self._last_exchange_ts else None,
            "latency_ms_last": lat[-1] if lat else None,
            "latency_ms_p50": statistics.median(lat) if lat else None,
            "latency_ms_p95": (sorted(lat)[int(0.95 * (len(lat) - 1))] if lat else None),
            "reconnect_attempts": self.reconnect_attempts,
            "alerts_enabled": self.alerts.data_ok,
            "alerts_block_reason": None if self.alerts.data_ok else self.alerts.data_status_reason,
            "candle_engine": dict(self.engine.stats.__dict__),
            "no_trade_identity": self.engine.no_identity_warning,
            "simulated": self.provider.simulated,
        })
        return p
