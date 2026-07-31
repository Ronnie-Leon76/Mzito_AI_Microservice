import asyncio

import pytest

from app.models import ConversationSession
from app.rate_limit import InMemoryRateLimiter, RateLimitExceeded
from app.session_store import InMemorySessionStore


@pytest.mark.asyncio
async def test_session_store_roundtrip_and_expiry():
    store = InMemorySessionStore(ttl_seconds=1)
    session = ConversationSession(key='k1', state='awaiting_ticket_description')
    await store.set(session)

    fetched = await store.get('k1')
    assert fetched is not None
    assert fetched.state == 'awaiting_ticket_description'

    await asyncio.sleep(1.1)
    expired = await store.get('k1')
    assert expired is None


@pytest.mark.asyncio
async def test_session_store_delete():
    store = InMemorySessionStore(ttl_seconds=60)
    await store.set(ConversationSession(key='k2'))
    await store.delete('k2')
    assert await store.get('k2') is None


def test_rate_limiter_blocks_after_limit():
    limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60)
    limiter.check('user-1')
    limiter.check('user-1')
    with pytest.raises(RateLimitExceeded):
        limiter.check('user-1')


def test_rate_limiter_tracks_users_independently():
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60)
    limiter.check('user-a')
    limiter.check('user-b')  # should not raise; separate bucket
