"""Candle Engine — constrói candles de 5 min exclusivamente a partir de NEGÓCIOS realizados.

Política (ver DATA_QUALITY.md):
* Bucket = candle de 5 min alinhado à abertura da sessão oficial (America/Sao_Paulo).
* Cotações (bid/ask) são ignoradas para OHLCV.
* Duplicidade: por ``trade_id``; na ausência dele, por ``sequence_number``. Sem nenhum dos dois,
  negócios idênticos NÃO são descartados (podem ser legítimos) — a limitação é registrada.
* Fora de ordem dentro de um candle aberto: aceito; O/C são definidos por (timestamp da bolsa, sequência).
* Fechamento: quando a marca d'água (maior timestamp de bolsa visto ou relógio) passa de
  ``fim do candle + close_grace``.
* Evento atrasado para candle já fechado: o candle é INVALIDADO (``CandleInvalidated``) e
  só volta a ser válido via ``rebuild`` com dados recuperados.
* Lacuna de sequência → candle marcado como suspeito + ``DataGapDetected``.
* Candle sem negócios → nenhum candle é criado (nenhum preço fictício) + ``DataGapDetected(no_trades)``.
* Conexão degradada durante o candle → candle fechado como INVALID.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from atlas.contracts.calendar import TradingCalendar, load_calendar
from atlas.core.timeframe import TIMEFRAME, TZ
from atlas.core.types import Bar, BarStatus, Quality, Symbol
from atlas.live.models import (
    CandleClosed, CandleInvalidated, CandleUpdated, DataGapDetected, EventType, LiveEvent, MarketEvent,
)


@dataclass
class _Acc:
    bucket: datetime
    open_key: tuple = ()
    open: float = 0.0
    close_key: tuple = ()
    close: float = 0.0
    high: float = float("-inf")
    low: float = float("inf")
    volume: float | None = 0.0
    trades: int = 0
    flags: set[str] = field(default_factory=set)
    synthetic: bool = False

    def add(self, ev: MarketEvent, arrival: int) -> None:
        key = (ev.exchange_timestamp, ev.sequence_number if ev.sequence_number is not None else -1, arrival)
        p = float(ev.price)
        if self.trades == 0 or key < self.open_key:
            self.open_key, self.open = key, p
        if self.trades == 0 or key > self.close_key:
            self.close_key, self.close = key, p
        self.high = max(self.high, p)
        self.low = min(self.low, p)
        if ev.quantity is None:
            self.volume = None
            self.flags.add("volume_unavailable")
        elif self.volume is not None:
            self.volume += float(ev.quantity)
        self.trades += 1
        self.synthetic = self.synthetic or ev.synthetic


@dataclass
class CandleEngineStats:
    accepted: int = 0
    duplicates: int = 0
    quotes_ignored: int = 0
    rejected_outside_session: int = 0
    rejected_other: int = 0
    late_events: int = 0
    sequence_gaps: int = 0
    out_of_order: int = 0


class CandleEngine:
    def __init__(self, symbol: Symbol | str, contract: str, source: str, calendar: TradingCalendar | None = None,
                 close_grace: timedelta = timedelta(seconds=2), max_closed_memory: int = 500):
        self.symbol = Symbol(symbol)
        self.contract = contract
        self.source = source
        self.cal = calendar or load_calendar()
        self.close_grace = close_grace
        self.max_closed_memory = max_closed_memory
        self.open_buckets: dict[datetime, _Acc] = {}
        self.closed: dict[datetime, Bar] = {}
        self.invalid_buckets: set[datetime] = set()
        self.watermark: datetime | None = None
        self.last_seq: int | None = None
        self._seen_ids: set[str] = set()
        self._seen_seq: set[int] = set()
        self._arrival = 0
        self._degraded_since: datetime | None = None
        self._last_checked_bucket: datetime | None = None
        self.stats = CandleEngineStats()
        self.no_identity_warning = False

    # ------------------------------------------------------------------
    def _bar_from(self, acc: _Acc, status: BarStatus, extra_flags: set[str] | None = None) -> Bar:
        flags = set(acc.flags) | (extra_flags or set())
        if status is BarStatus.INVALID:
            quality = Quality.INVALID
        elif flags - {"volume_unavailable", "rebuilt"}:
            quality = Quality.SUSPECT
        else:
            quality = Quality.OK
        return Bar(self.symbol, self.contract, acc.bucket, acc.open, acc.high, acc.low, acc.close, acc.volume,
                   acc.trades, status, quality, tuple(sorted(flags)), self.source, synthetic=acc.synthetic)

    def mark_degraded(self, since: datetime) -> None:
        if self._degraded_since is None:
            self._degraded_since = since

    def mark_recovered(self) -> None:
        self._degraded_since = None

    @property
    def degraded(self) -> bool:
        return self._degraded_since is not None

    # ------------------------------------------------------------------
    def ingest(self, ev: MarketEvent) -> list[LiveEvent]:
        if ev.event_type is not EventType.TRADE:
            self.stats.quotes_ignored += 1
            return []
        if ev.price is None or ev.price <= 0 or (ev.contract and ev.contract != self.contract) or ev.symbol != self.symbol.value:
            self.stats.rejected_other += 1
            return []
        out: list[LiveEvent] = []
        ts = ev.exchange_timestamp.astimezone(TZ)
        sess = self.cal.session_for_ts(self.symbol, ts)
        if sess is None:
            self.stats.rejected_outside_session += 1
            return []
        # duplicidade
        if ev.trade_id is not None:
            if ev.trade_id in self._seen_ids:
                self.stats.duplicates += 1
                return []
            self._seen_ids.add(ev.trade_id)
        elif ev.sequence_number is not None:
            if ev.sequence_number in self._seen_seq:
                self.stats.duplicates += 1
                return []
        else:
            self.no_identity_warning = True
        bucket = sess.bucket_open(ts)
        # sequência
        seq_gap = False
        if ev.sequence_number is not None:
            self._seen_seq.add(ev.sequence_number)
            if self.last_seq is not None:
                if ev.sequence_number > self.last_seq + 1:
                    seq_gap = True
                    self.stats.sequence_gaps += 1
                    out.append(DataGapDetected(self.symbol.value, self.contract, ts, None, "sequence_gap",
                                               {"expected": self.last_seq + 1, "received": ev.sequence_number}))
                elif ev.sequence_number < self.last_seq:
                    self.stats.out_of_order += 1
            self.last_seq = ev.sequence_number if self.last_seq is None else max(self.last_seq, ev.sequence_number)

        if bucket in self.closed or bucket in self.invalid_buckets:
            self.stats.late_events += 1
            if bucket in self.closed:
                old = self.closed.pop(bucket)
                self.invalid_buckets.add(bucket)
                out.append(CandleInvalidated(old.with_(status=BarStatus.INVALID, quality=Quality.INVALID,
                                                       quality_flags=tuple(sorted(set(old.quality_flags) | {"late_event"}))),
                                             "late_event"))
            out.append(DataGapDetected(self.symbol.value, self.contract, bucket, bucket + TIMEFRAME, "late_event",
                                       {"event_ts": ts.isoformat()}))
            return out

        acc = self.open_buckets.get(bucket)
        if acc is None:
            acc = self.open_buckets[bucket] = _Acc(bucket)
        if seq_gap:
            acc.flags.add("sequence_gap")
        if self._degraded_since is not None:
            acc.flags.add("connection_degraded")
        self._arrival += 1
        acc.add(ev, self._arrival)
        self.stats.accepted += 1
        out.append(CandleUpdated(self._bar_from(acc, BarStatus.FORMING)))
        if self.watermark is None or ts > self.watermark:
            self.watermark = ts
        out.extend(self._close_ready(self.watermark))
        return out

    def advance_clock(self, now: datetime) -> list[LiveEvent]:
        """Avança o relógio (horário da bolsa). Fecha candles vencidos e detecta candles sem negócios."""
        now = now.astimezone(TZ)
        if self.watermark is None or now > self.watermark:
            self.watermark = now
        return self._close_ready(self.watermark)

    def _close_ready(self, wm: datetime) -> list[LiveEvent]:
        out: list[LiveEvent] = []
        for bucket in sorted(self.open_buckets):
            if bucket + TIMEFRAME + self.close_grace > wm:
                break
            acc = self.open_buckets.pop(bucket)
            if self._degraded_since is not None and self._degraded_since < bucket + TIMEFRAME:
                acc.flags.add("connection_degraded")
            if "connection_degraded" in acc.flags:
                bar = self._bar_from(acc, BarStatus.INVALID)
                self.invalid_buckets.add(bucket)
                out.append(CandleInvalidated(bar, "connection_degraded"))
            else:
                bar = self._bar_from(acc, BarStatus.CLOSED)
                self.closed[bucket] = bar
                out.append(CandleClosed(bar))
        out.extend(self._detect_empty(wm))
        self._trim()
        return out

    def _detect_empty(self, wm: datetime) -> list[LiveEvent]:
        """Candles da sessão já vencidos sem nenhum negócio → DataGapDetected (sem criar candle)."""
        out: list[LiveEvent] = []
        local = wm.astimezone(TZ)
        sess = self.cal.session(self.symbol, local.date())
        if sess is None:
            return out
        for b in sess.bucket_opens():
            if b + TIMEFRAME + self.close_grace > wm:
                break
            if self._last_checked_bucket is not None and b <= self._last_checked_bucket:
                continue
            self._last_checked_bucket = b
            if b in self.closed or b in self.invalid_buckets or b in self.open_buckets:
                continue
            if self.watermark is not None and self.stats.accepted == 0 and not self.closed:
                # antes do primeiro negócio recebido não afirmamos lacuna de mercado: registramos "sem dados"
                reason = "no_data_received"
            else:
                reason = "connection_lost" if self._degraded_since is not None else "no_trades"
            self.invalid_buckets.add(b)
            out.append(DataGapDetected(self.symbol.value, self.contract, b, b + TIMEFRAME, reason))
        return out

    def _trim(self) -> None:
        if len(self.closed) > self.max_closed_memory:
            for k in sorted(self.closed)[: len(self.closed) - self.max_closed_memory]:
                del self.closed[k]

    # ------------------------------------------------------------------
    def rebuild(self, events: list[MarketEvent], start: datetime, end: datetime) -> list[LiveEvent]:
        """Reconstrói os candles do intervalo [start, end) a partir de eventos recuperados do provedor.

        ``start`` deve ser a ABERTURA do primeiro candle afetado, e o provedor deve garantir que os eventos
        cobrem todo o intervalo. Candles inteiramente anteriores a ``end`` são fechados com a flag ``rebuilt``;
        o candle que contém ``end`` volta a ser acumulado normalmente (os próximos negócios do fluxo o completam).
        """
        start, end = start.astimezone(TZ), end.astimezone(TZ)
        accs: dict[datetime, _Acc] = {}
        ids: set[str] = set()
        n = 0
        for ev in sorted(events, key=lambda e: (e.exchange_timestamp, e.sequence_number or 0)):
            if ev.event_type is not EventType.TRADE or ev.price is None:
                continue
            ts = ev.exchange_timestamp.astimezone(TZ)
            if not (start <= ts < end):
                continue
            sess = self.cal.session_for_ts(self.symbol, ts)
            if sess is None:
                continue
            if ev.trade_id is not None:
                if ev.trade_id in ids:
                    continue
                ids.add(ev.trade_id)
            if ev.sequence_number is not None:
                self._seen_seq.add(ev.sequence_number)
                self.last_seq = ev.sequence_number if self.last_seq is None else max(self.last_seq, ev.sequence_number)
            b = sess.bucket_open(ts)
            n += 1
            accs.setdefault(b, _Acc(b)).add(ev, n)
        self._seen_ids |= ids
        # descarta acumuladores parciais/degradados do intervalo afetado
        for b in [b for b in self.open_buckets if start <= b < end]:
            del self.open_buckets[b]
        out: list[LiveEvent] = []
        ref = max(self.watermark or end, end)
        self.watermark = ref
        for b in sorted(accs):
            acc = accs[b]
            acc.flags.add("rebuilt")
            if b + TIMEFRAME <= end and b + TIMEFRAME + self.close_grace <= ref:
                self.invalid_buckets.discard(b)
                bar = self._bar_from(acc, BarStatus.CLOSED)
                self.closed[b] = bar
                out.append(CandleClosed(bar))
            else:
                self.invalid_buckets.discard(b)
                self.open_buckets[b] = acc  # candle ainda em formação
        return out
