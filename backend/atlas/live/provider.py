"""Interface abstrata ``MarketDataProvider``.

Qualquer fornecedor (Cedro, outro vendor, replay) implementa esta interface;
o restante do ATLAS depende apenas dela. Nenhum método envia ordens.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime

from atlas.core.types import Bar
from atlas.live.models import MarketEvent, ProviderStatus

TradeHandler = Callable[[MarketEvent], Awaitable[None] | None]
QuoteHandler = Callable[[MarketEvent], Awaitable[None] | None]
ErrorHandler = Callable[[Exception], Awaitable[None] | None]


class ProviderError(Exception):
    """Erro genérico de provedor."""


class AuthenticationError(ProviderError):
    """Credenciais rejeitadas pelo provedor."""


class ProviderNotConfigured(ProviderError):
    """Provedor sem documentação/credenciais suficientes para operar."""


class NotSupported(ProviderError):
    """Operação não suportada por este provedor (ex.: conexão ao vivo em CSVProvider)."""


class MarketDataProvider(ABC):
    name: str = "abstract"
    simulated: bool = False
    supports_recovery: bool = False

    def __init__(self) -> None:
        self._trade_handlers: list[TradeHandler] = []
        self._quote_handlers: list[QuoteHandler] = []
        self._error_handlers: list[ErrorHandler] = []

    # ---- ciclo de vida ----
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def subscribe(self, symbol: str) -> None:
        """``symbol`` deve ser o código completo do contrato (ex.: WINZ26)."""

    @abstractmethod
    async def unsubscribe(self, symbol: str) -> None: ...

    @abstractmethod
    def get_status(self) -> ProviderStatus: ...

    @abstractmethod
    async def get_historical_data(self, symbol: str, start: datetime, end: datetime) -> list[Bar]: ...

    async def recover_events(self, symbol: str, start: datetime, end: datetime) -> list[MarketEvent]:
        """Recupera negócios de um intervalo (se o provedor suportar)."""
        raise NotSupported(f"{self.name} não suporta recuperação de eventos")

    # ---- callbacks ----
    def on_trade(self, handler: TradeHandler) -> None:
        self._trade_handlers.append(handler)

    def on_quote(self, handler: QuoteHandler) -> None:
        self._quote_handlers.append(handler)

    def on_error(self, handler: ErrorHandler) -> None:
        self._error_handlers.append(handler)

    async def _emit(self, handlers, arg) -> None:
        for h in handlers:
            r = h(arg)
            if hasattr(r, "__await__"):
                await r

    async def _emit_trade(self, ev: MarketEvent) -> None:
        await self._emit(self._trade_handlers, ev)

    async def _emit_quote(self, ev: MarketEvent) -> None:
        await self._emit(self._quote_handlers, ev)

    async def _emit_error(self, exc: Exception) -> None:
        await self._emit(self._error_handlers, exc)
