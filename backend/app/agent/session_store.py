"""Conversation state per session.

In-memory implementation behind a small protocol. In production this would be Redis
(fast, TTL-native, shared across API replicas); the runtime only depends on the protocol.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Session:
    id: str
    messages: list[dict[str, Any]] = field(default_factory=list)  # canonical history, no system prompt
    focus_airports: list[str] = field(default_factory=list)  # airports discussed most recently
    last_tools: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class SessionStore(Protocol):
    def get_or_create(self, session_id: str | None) -> Session: ...
    def save(self, session: Session) -> None: ...
    def delete(self, session_id: str) -> None: ...


class InMemorySessionStore:
    def __init__(self, max_sessions: int = 500, ttl_s: int = 6 * 3600):
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self.max_sessions = max_sessions
        self.ttl_s = ttl_s

    def get_or_create(self, session_id: str | None) -> Session:
        self._evict()
        if session_id and session_id in self._sessions:
            self._sessions.move_to_end(session_id)
            return self._sessions[session_id]
        session = Session(id=session_id or uuid.uuid4().hex)
        self._sessions[session.id] = session
        return session

    def save(self, session: Session) -> None:
        session.updated_at = time.time()
        self._sessions[session.id] = session
        self._sessions.move_to_end(session.id)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def _evict(self) -> None:
        now = time.time()
        for sid in [s for s, v in self._sessions.items() if now - v.updated_at > self.ttl_s]:
            del self._sessions[sid]
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
