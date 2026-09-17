"""Redis client used for ephemeral call/session state, presence, locks."""
from __future__ import annotations

import json
from typing import Any, Optional

import redis.asyncio as redis

from app.config import settings

_pool: Optional[redis.ConnectionPool] = None


def _get_pool() -> redis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=64,
        )
    return _pool


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_get_pool())


# ---------------------------------------------------------------------------
# Active-call state helpers — never store audio here.
# ---------------------------------------------------------------------------

CALL_KEY = "call:{call_id}:state"
PARTICIPANT_KEY = "call:{call_id}:participant:{user_id}"
PRESENCE_KEY = "call:{call_id}:presence"
RATE_LIMIT_KEY = "rl:{user_id}:{action}"
AGENT_LOCK_KEY = "call:{call_id}:agent_lock"

# Queue used to tell the translation-agent process which room to join.
# The agent BLPOPs this list and connects to the specified room using
# the embedded token + metadata.
AGENT_JOIN_QUEUE = "agent:join_queue"


async def enqueue_agent_join(
    call_id: str,
    room_name: str,
    livekit_url: str,
    agent_token: str,
    participants: list[dict],
) -> None:
    """Push a 'join this room' command for the translation agent."""
    payload = json.dumps({
        "call_id": call_id,
        "room_name": room_name,
        "livekit_url": livekit_url,
        "agent_token": agent_token,
        "participants": participants,  # [{"participant_id": "...", "speak": "en", "hear": "hi"}]
    })
    r = get_redis()
    await r.lpush(AGENT_JOIN_QUEUE, payload)


async def set_call_state(call_id: str, data: dict[str, Any], ttl: int = 86400) -> None:
    r = get_redis()
    await r.set(CALL_KEY.format(call_id=call_id), json.dumps(data), ex=ttl)


async def get_call_state(call_id: str) -> Optional[dict[str, Any]]:
    r = get_redis()
    raw = await r.get(CALL_KEY.format(call_id=call_id))
    return json.loads(raw) if raw else None


async def delete_call_state(call_id: str) -> None:
    r = get_redis()
    await r.delete(CALL_KEY.format(call_id=call_id))


async def add_presence(call_id: str, user_id: str, ttl: int = 60) -> None:
    r = get_redis()
    await r.sadd(PRESENCE_KEY.format(call_id=call_id), user_id)
    await r.expire(PRESENCE_KEY.format(call_id=call_id), ttl)


async def remove_presence(call_id: str, user_id: str) -> None:
    r = get_redis()
    await r.srem(PRESENCE_KEY.format(call_id=call_id), user_id)


async def list_presence(call_id: str) -> list[str]:
    r = get_redis()
    return list(await r.smembers(PRESENCE_KEY.format(call_id=call_id)))


async def rate_limit(user_id: str, action: str, limit: int = 30, window: int = 60) -> bool:
    """Returns True if request is allowed, False if rate-limited."""
    r = get_redis()
    key = RATE_LIMIT_KEY.format(user_id=user_id, action=action)
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, window)
    return count <= limit


async def acquire_agent_lock(call_id: str, ttl: int = 30) -> bool:
    """Ensure only one translation agent handles a call at a time."""
    r = get_redis()
    # SET NX with expiry — basic distributed lock
    return bool(await r.set(AGENT_LOCK_KEY.format(call_id=call_id), "1", nx=True, ex=ttl))


async def release_agent_lock(call_id: str) -> None:
    r = get_redis()
    await r.delete(AGENT_LOCK_KEY.format(call_id=call_id))
