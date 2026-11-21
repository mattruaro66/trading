"""Implementações de ``MarketDataProvider``: ReplayProvider, CSVProvider e CedroProvider (pendente)."""
from __future__ import annotations

import asyncio
import io
from datetime import datetime
from pathlib import Path

import pandas as pd

from atlas.config import Settings, get_settings
from atlas.core.timeframe import TZ
from atlas.core.types import Bar
from atlas.live.models import ConnectionState, EventType, MarketEvent, ProviderStatus
from atlas.live.provider import (
    AuthenticationError, MarketDataProvider, NotSupported, ProviderError, ProviderNotConfigured,
)
from atlas.market_data.importers import import_bars


# ======================================================================
class ReplayProvider(MarketDataProvider):
    """Reproduz eventos de negócios históricos em ordem de chegada, com a MESMA interface de um feed real.

    É sempre identificado como ``simulated=True``. Recursos de teste (injeção de falhas) existem apenas
    para validar a robustez do pipeline.
    """

    name = "replay"
    simulated = True
    supports_recovery = True  # a fonte local permite "recuperar" o intervalo perdido

    def __init__(self, events: list[MarketEvent], speed: float = 0.0, *, fail_after: int | None = None,
                 drop_on_fail: int = 0, reject_auth: bool = False):
        super().__init__()
        self._all = list(events)
        self.speed = speed  # 0 = o mais rápido possível; 1 = tempo real; 10 = 10x
        self.fail_after = fail_after
        self.drop_on_fail = drop_on_fail  # eventos "perdidos" durante a queda simulada
        self.reject_auth = reject_auth
        self._pos = 0
        self._subs: set[str] = set()
        self._state = ConnectionState.DISCONNECTED
        self._task: asyncio.Task | None = None
        self._last_event_at: datetime | None = None
        self._last_exchange: datetime | None = None
        self._clock: datetime | None = None
        self._failed_once = False
        self.reconnects = 0
        self.done = asyncio.Event()

    @classmethod
    def from_csv(cls, path: str | Path | bytes, symbol: str, contract: str, **kw) -> "ReplayProvider":
        """CSV de negócios: exchange_timestamp, price[, quantity][, trade_id][, sequence_number][, received_timestamp]."""
        df = pd.read_csv(io.BytesIO(path) if isinstance(path, bytes) else path)
        evs = []
        for r in df.itertuples(index=False):
            ts = pd.Timestamp(r.exchange_timestamp)
            ts = ts.tz_localize(TZ) if ts.tzinfo is None else ts.tz_convert(TZ)
            rcv = getattr(r, "received_timestamp", None)
            rcv = ts if rcv is None or pd.isna(rcv) else pd.Timestamp(rcv)
            rcv = rcv.tz_localize(TZ) if rcv.tzinfo is None else rcv.tz_convert(TZ)

            def opt(name, cast):
                v = getattr(r, name, None)
                return None if v is None or pd.isna(v) else cast(v)

            evs.append(MarketEvent("replay", symbol, contract, EventType.TRADE, ts.to_pydatetime(), rcv.to_pydatetime(),
                                   float(r.price), opt("quantity", float), opt("trade_id", str), opt("sequence_number", int)))
        return cls(evs, **kw)

    def clock(self) -> datetime | None:
        """Relógio simulado: horário do último evento emitido; desconectado, o horário do próximo evento
        (ponto em que o fluxo será retomado)."""
        if self._state is not ConnectionState.CONNECTED and self._pos < len(self._all):
            return self._all[self._pos].exchange_timestamp
        return self._clock

    async def connect(self) -> None:
        if self.reject_auth:
            self._state = ConnectionState.FAILED
            raise AuthenticationError("Credenciais rejeitadas (falha simulada para teste)")
        self._state = ConnectionState.CONNECTED

    async def disconnect(self) -> None:
        if self._task and not self._task.done() and self._task is not asyncio.current_task():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._state = ConnectionState.DISCONNECTED

    async def subscribe(self, symbol: str) -> None:
        if self._state is not ConnectionState.CONNECTED:
            raise ProviderError("Não conectado")
        self._subs.add(symbol)
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def unsubscribe(self, symbol: str) -> None:
        self._subs.discard(symbol)

    async def _run(self) -> None:
        prev_ts = None
        try:
            while self._pos < len(self._all):
                ev = self._all[self._pos]
                if self.fail_after is not None and not self._failed_once and self._pos >= self.fail_after:
                    self._failed_once = True
                    self._pos += self.drop_on_fail  # eventos perdidos durante a queda
                    self._state = ConnectionState.DISCONNECTED
                    await self._emit_error(ConnectionError("Conexão perdida (falha simulada para teste)"))
                    return
                if self.speed > 0 and prev_ts is not None:
                    await asyncio.sleep(max(0.0, (ev.exchange_timestamp - prev_ts).total_seconds() / self.speed))
                prev_ts = ev.exchange_timestamp
                self._pos += 1
                if ev.contract not in self._subs and ev.contract is not None:
                    continue
                self._clock = ev.exchange_timestamp
                self._last_event_at = datetime.now(TZ)
                self._last_exchange = ev.exchange_timestamp
                if ev.event_type is EventType.TRADE:
                    await self._emit_trade(ev)
                else:
                    await self._emit_quote(ev)
                if self.speed == 0 and self._pos % 200 == 0:
                    await asyncio.sleep(0)
        finally:
            if self._pos >= len(self._all):
                self.done.set()

    async def recover_events(self, symbol: str, start: datetime, end: datetime) -> list[MarketEvent]:
        return [e for e in self._all if e.contract == symbol and start <= e.exchange_timestamp < end]

    async def get_historical_data(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        raise NotSupported("ReplayProvider fornece negócios; use o Candle Engine para agregá-los")

    def get_status(self) -> ProviderStatus:
        return ProviderStatus(self.name, self._state, True, sorted(self._subs), self._last_event_at, self._last_exchange,
                              message="SIMULAÇÃO: reprodução de eventos históricos, não é um feed ao vivo")


# ======================================================================
class CSVProvider(MarketDataProvider):
    """Provedor de candles históricos importados. NÃO simula conectividade ao vivo."""

    name = "csv"
    simulated = False

    def __init__(self, path: str | Path, symbol: str, contract: str, **import_kw):
        super().__init__()
        self.path = Path(path)
        self.symbol = symbol
        self.contract = contract
        self._import_kw = import_kw
        self._bars: list[Bar] | None = None

    async def connect(self) -> None:
        raise NotSupported("CSVProvider é somente histórico: não há conexão ao vivo")

    async def disconnect(self) -> None:
        return None

    async def subscribe(self, symbol: str) -> None:
        raise NotSupported("CSVProvider é somente histórico: assinatura ao vivo indisponível")

    async def unsubscribe(self, symbol: str) -> None:
        raise NotSupported("CSVProvider é somente histórico")

    async def get_historical_data(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        if symbol != self.contract:
            raise ProviderError(f"Arquivo contém {self.contract}, não {symbol}")
        if self._bars is None:
            self._bars = import_bars(self.path, self.symbol, self.contract, **self._import_kw).bars
        return [b for b in self._bars if start <= b.ts_open <= end]

    def get_status(self) -> ProviderStatus:
        return ProviderStatus(self.name, ConnectionState.HISTORICAL_ONLY, False, [],
                              message=f"Arquivo histórico {self.path.name}")


# ======================================================================
class CedroProvider(MarketDataProvider):
    """Adaptador Cedro Market Data — PENDENTE.

    A documentação oficial do fornecedor não pôde ser consultada (ver PROVIDER_INTEGRATION.md).
    Por regra do projeto, nenhum endpoint, comando, formato de mensagem ou parâmetro foi presumido.
    Todos os métodos falham explicitamente até que a integração seja implementada a partir da
    documentação oficial e testada com credenciais autorizadas.
    """

    name = "cedro"
    simulated = False
    PENDING_MSG = ("Integração Cedro pendente: implementar a partir da documentação oficial do fornecedor e testar com "
                   "credenciais autorizadas (ver PROVIDER_INTEGRATION.md). Nenhum endpoint foi presumido.")

    def __init__(self, settings: Settings | None = None):
        super().__init__()
        self.settings = settings or get_settings()

    @property
    def credentials_present(self) -> bool:
        s = self.settings
        return bool(s.cedro_username and s.cedro_password and s.cedro_endpoint)

    async def connect(self) -> None:
        raise ProviderNotConfigured(self.PENDING_MSG)

    async def disconnect(self) -> None:
        return None

    async def subscribe(self, symbol: str) -> None:
        raise ProviderNotConfigured(self.PENDING_MSG)

    async def unsubscribe(self, symbol: str) -> None:
        raise ProviderNotConfigured(self.PENDING_MSG)

    async def get_historical_data(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        raise ProviderNotConfigured(self.PENDING_MSG)

    def get_status(self) -> ProviderStatus:
        msg = self.PENDING_MSG + (" Credenciais presentes no ambiente." if self.credentials_present else
                                  " Credenciais não configuradas.")
        return ProviderStatus(self.name, ConnectionState.NOT_CONFIGURED, False, [], message=msg)
