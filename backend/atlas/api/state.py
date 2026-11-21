"""Estado da aplicação (repositório, sessões de replay, monitor live, hub WebSocket)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from atlas.config import Settings
from atlas.live.monitor import LiveMonitor
from atlas.persistence.repository import Repository
from atlas.replay.session import ReplaySession


class WsHub:
    """Difusão de mensagens para clientes WebSocket (fila por cliente, descarta se o cliente estiver lento)."""

    def __init__(self, max_queue: int = 1000):
        self.clients: set[asyncio.Queue] = set()
        self.max_queue = max_queue

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.max_queue)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.clients.discard(q)

    async def publish(self, msg: dict) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


@dataclass
class AppState:
    settings: Settings
    repo: Repository
    hub: WsHub = field(default_factory=WsHub)
    replays: dict[str, ReplaySession] = field(default_factory=dict)
    live: LiveMonitor | None = None
    live_task: asyncio.Task | None = None
