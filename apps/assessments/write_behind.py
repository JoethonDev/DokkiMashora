"""Versioned Redis pending-write protocol for diploma exam autosave."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache

import redis
from django.conf import settings


def enabled() -> bool:
    return bool(getattr(settings, "ASSESSMENT_AUTOSAVE_WRITE_BEHIND", False))


@lru_cache(maxsize=1)
def _client():
    url = getattr(settings, "CACHE_URL", "")
    if not url:
        return None
    return redis.Redis.from_url(url, decode_responses=True)


def _keys(attempt_id: int) -> tuple[str, str, str]:
    prefix = f"assessment-attempt:{attempt_id}:pending-snapshot:v1"
    return prefix, f"{prefix}:generation", f"{prefix}:lock"


def _schedule_key(attempt_id: int) -> str:
    return f"assessment-attempt:{attempt_id}:pending-snapshot:v1:scheduled"


def _closed_key(attempt_id: int) -> str:
    return f"assessment-attempt:{attempt_id}:pending-snapshot:v1:closed"


def _pending_attempts_key() -> str:
    return "assessment-attempts:pending-snapshot:v1"


def enqueue(attempt_id: int, values: dict[str, object]) -> int:
    """Merge a validated delta and atomically return its new generation."""
    client = _client()
    if client is None:
        raise RuntimeError("Redis cache is not configured")
    snapshot_key, generation_key, _ = _keys(attempt_id)
    closed_key = _closed_key(attempt_id)
    args = [snapshot_key, generation_key, closed_key, len(values)]
    for field, value in values.items():
        args.extend((field, json.dumps(value)))
    script = """
    local hash_key = ARGV[1]
    local generation_key = ARGV[2]
    local closed_key = ARGV[3]
    if redis.call('EXISTS', closed_key) == 1 then return 0 end
    local count = tonumber(ARGV[4])
    local index = 5
    for i = 1, count do
        redis.call('HSET', hash_key, ARGV[index], ARGV[index + 1])
        index = index + 2
    end
    return redis.call('INCR', generation_key)
    """
    generation = int(client.eval(script, 0, *args))
    if generation == 0:
        return 0
    register_pending(attempt_id)
    return generation


def register_pending(attempt_id: int) -> None:
    client = _client()
    if client is not None:
        client.sadd(_pending_attempts_key(), attempt_id)


def claim_pending(limit: int = 50) -> list[int]:
    client = _client()
    if client is None:
        return []
    values = client.spop(_pending_attempts_key(), count=limit) or []
    return [int(value) for value in values]


def requeue_pending(attempt_ids: list[int]) -> None:
    client = _client()
    if client is not None and attempt_ids:
        client.sadd(_pending_attempts_key(), *attempt_ids)


def discard(attempt_id: int) -> None:
    """Remove pending state for an attempt deleted before its task ran."""
    client = _client()
    if client is not None:
        snapshot_key, generation_key, lock_key = _keys(attempt_id)
        client.delete(snapshot_key, generation_key, lock_key, _schedule_key(attempt_id))
        client.srem(_pending_attempts_key(), attempt_id)


def close(attempt_id: int, ttl: int = 7 * 24 * 60 * 60) -> None:
    """Close the Redis edit window after the DB submission commits."""
    client = _client()
    if client is not None:
        client.set(_closed_key(attempt_id), "1", ex=ttl)
        discard(attempt_id)


def _read(client, attempt_id: int) -> tuple[int, dict[str, object]]:
    snapshot_key, generation_key, _ = _keys(attempt_id)
    generation = int(client.get(generation_key) or 0)
    raw_values = client.hgetall(snapshot_key)
    values = {key: json.loads(value) for key, value in raw_values.items()}
    return generation, values


def _clear_if_current(client, attempt_id: int, generation: int) -> None:
    snapshot_key, generation_key, _ = _keys(attempt_id)
    script = """
    if redis.call('GET', KEYS[2]) == ARGV[1] then
        redis.call('DEL', KEYS[1], KEYS[2])
        return 1
    end
    return 0
    """
    client.eval(script, 2, snapshot_key, generation_key, str(generation))


def claim_schedule(attempt_id: int) -> bool:
    client = _client()
    if client is None:
        return False
    return bool(client.set(_schedule_key(attempt_id), "1", nx=True, ex=10))


def release_schedule(attempt_id: int) -> None:
    client = _client()
    if client is not None:
        client.delete(_schedule_key(attempt_id))


def has_pending(attempt_id: int) -> bool:
    client = _client()
    if client is None:
        return False
    snapshot_key, _, _ = _keys(attempt_id)
    return bool(client.exists(snapshot_key))


def latest_values(attempt_id: int) -> dict[str, object]:
    """Return the current Redis pending delta for take-page hydration."""
    client = _client()
    if client is None:
        return {}
    _, values = _read(client, attempt_id)
    return values


def drain(attempt_id: int, persist: Callable[[dict[str, object]], None]) -> bool:
    """Persist the current pending state while holding its Redis lock."""
    client = _client()
    if client is None:
        return False
    _, _, lock_key = _keys(attempt_id)
    with client.lock(lock_key, timeout=60, blocking_timeout=10):
        generation, values = _read(client, attempt_id)
        if not values:
            return False
        persist(values)
        _clear_if_current(client, attempt_id, generation)
    return True


def flush(attempt_id: int, generation: int, persist: Callable[[dict[str, object]], None]) -> bool:
    """Flush a claimed generation without clearing newer pending deltas."""
    client = _client()
    if client is None:
        raise RuntimeError("Redis cache is not configured")
    _, _, lock_key = _keys(attempt_id)
    with client.lock(lock_key, timeout=60, blocking_timeout=10):
        current_generation, values = _read(client, attempt_id)
        if not values or current_generation < generation:
            return False
        persist(values)
        _clear_if_current(client, attempt_id, current_generation)
    return True


def flush_batch(
    attempt_ids: list[int],
    persist: Callable[[dict[int, dict[str, object]]], None],
) -> list[int]:
    """Read and flush several attempt snapshots while holding their Redis locks."""
    client = _client()
    if client is None:
        raise RuntimeError("Redis cache is not configured")
    flushed: dict[int, dict[str, object]] = {}
    generations: dict[int, int] = {}
    locks = []
    try:
        for attempt_id in sorted(set(attempt_ids)):
            _, _, lock_key = _keys(attempt_id)
            lock = client.lock(lock_key, timeout=60, blocking_timeout=10)
            lock.acquire()
            locks.append(lock)
            generation, values = _read(client, attempt_id)
            if values:
                flushed[attempt_id] = values
                generations[attempt_id] = generation
        if flushed:
            persist(flushed)
            for attempt_id, generation in generations.items():
                _clear_if_current(client, attempt_id, generation)
        return list(flushed)
    finally:
        for lock in reversed(locks):
            lock.release()
