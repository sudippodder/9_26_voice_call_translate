"""Worker that drains Redis usage/metrics queues into PostgreSQL.

In V1 it polls a Redis list `usage:queue` and inserts rows in `usage`/`call_quality_metrics`.
Runs as a separate container for horizontal scaling.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

import asyncpg
import redis.asyncio as aioredis
from structlog import get_logger

log = get_logger("worker")


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://vtranslate:vtranslate@localhost:5432/voice_translator",
).replace("postgresql+asyncpg://", "postgresql://")  # asyncpg wants plain postgresql://

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

USAGE_QUEUE = "usage:queue"
METRICS_QUEUE = "metrics:queue"


async def usage_loop(pg: asyncpg.Pool, r: aioredis.Redis) -> None:
    while True:
        try:
            item = await r.brpop(USAGE_QUEUE, timeout=5)
            if not item:
                continue
            _, raw = item
            payload = json.loads(raw)
            async with pg.acquire() as conn:
                await conn.execute(
                    "INSERT INTO usage (id, user_id, call_id, audio_seconds, translation_seconds) "
                    "VALUES (gen_random_uuid()::text, $1, $2, $3, $4)",
                    payload["user_id"],
                    payload.get("call_id"),
                    float(payload.get("audio_seconds", 0.0)),
                    float(payload.get("translation_seconds", 0.0)),
                )
            log.info("usage_flushed", user_id=payload["user_id"])
        except Exception as e:  # noqa: BLE001
            log.error("usage_loop_error", error=str(e))
            await asyncio.sleep(2)


async def metrics_loop(pg: asyncpg.Pool, r: aioredis.Redis) -> None:
    while True:
        try:
            item = await r.brpop(METRICS_QUEUE, timeout=5)
            if not item:
                continue
            _, raw = item
            payload = json.loads(raw)
            async with pg.acquire() as conn:
                await conn.execute(
                    "INSERT INTO call_quality_metrics (id, call_id, participant_id, metric_name, metric_value, payload) "
                    "VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5)",
                    payload["call_id"],
                    payload["participant_id"],
                    payload["metric_name"],
                    float(payload["metric_value"]),
                    json.dumps(payload.get("payload") or {}),
                )
        except Exception as e:  # noqa: BLE001
            log.error("metrics_loop_error", error=str(e))
            await asyncio.sleep(2)


async def main() -> None:
    log.info("worker_starting")
    pg = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    await asyncio.gather(usage_loop(pg, r), metrics_loop(pg, r))


if __name__ == "__main__":
    asyncio.run(main())
