"""Short-lived conversation state so the bot can hold multi-turn exchanges
(e.g. "create ticket" -> "what's the issue?" -> user reply -> confirm).

Two backends:
- InMemorySessionStore: default, fine for a single replica.
- RedisSessionStore: use when running more than one uvicorn/container
  replica, so a user's follow-up reply lands on any instance.

Sessions are keyed by chat_id+user id, hold no ticket content once resolved,
and expire automatically (session_ttl_seconds) so nothing lingers.
"""
import asyncio
import json
import time
from abc import ABC, abstractmethod

from .models import ConversationSession


class SessionStore(ABC):
    @abstractmethod
    async def get(self, key: str) -> ConversationSession | None: ...

    @abstractmethod
    async def set(self, session: ConversationSession) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...


class InMemorySessionStore(SessionStore):
    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._data: dict[str, ConversationSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> ConversationSession | None:
        async with self._lock:
            session = self._data.get(key)
            if session is None:
                return None
            if session.is_expired(self.ttl_seconds):
                del self._data[key]
                return None
            return session

    async def set(self, session: ConversationSession) -> None:
        session.updated_at = time.time()
        async with self._lock:
            self._data[session.key] = session
            self._sweep_locked()

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    def _sweep_locked(self) -> None:
        # Opportunistic cleanup so memory does not grow unbounded between requests.
        expired = [k for k, v in self._data.items() if v.is_expired(self.ttl_seconds)]
        for k in expired:
            del self._data[k]


class RedisSessionStore(SessionStore):
    def __init__(self, redis_client, ttl_seconds: int, prefix: str = 'cliq:session:'):
        self.redis = redis_client
        self.ttl_seconds = ttl_seconds
        self.prefix = prefix

    def _key(self, key: str) -> str:
        return f'{self.prefix}{key}'

    async def get(self, key: str) -> ConversationSession | None:
        raw = await self.redis.get(self._key(key))
        if not raw:
            return None
        return ConversationSession.model_validate(json.loads(raw))

    async def set(self, session: ConversationSession) -> None:
        session.updated_at = time.time()
        await self.redis.set(self._key(session.key), session.model_dump_json(), ex=self.ttl_seconds)

    async def delete(self, key: str) -> None:
        await self.redis.delete(self._key(key))


def build_session_store(settings) -> SessionStore:
    if settings.session_backend == 'redis' and settings.redis_url:
        import redis.asyncio as redis  # imported lazily so redis stays optional

        client = redis.from_url(settings.redis_url, decode_responses=True)
        return RedisSessionStore(client, settings.session_ttl_seconds)
    return InMemorySessionStore(settings.session_ttl_seconds)
