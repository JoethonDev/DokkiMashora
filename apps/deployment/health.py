"""Health probe: DB ping, cache write/read, storage reachability."""

from __future__ import annotations

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection


def _check_db() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # pragma: no cover - defensive
        return False
    return True


def _check_cache() -> bool:
    try:
        cache.set("__deployment_health__", "ok", timeout=10)
        return cache.get("__deployment_health__") == "ok"
    except Exception:  # pragma: no cover - defensive
        return False


def _check_storage() -> bool:
    name = "__deployment_health__.txt"
    try:
        if default_storage.exists(name):
            default_storage.delete(name)
        default_storage.save(name, ContentFile(b"ok"))
        ok = default_storage.exists(name) and default_storage.open(name).read() == b"ok"
        default_storage.delete(name)
        return ok
    except Exception:  # pragma: no cover - defensive
        return False


def health_check() -> dict:
    db = _check_db()
    cache_ok = _check_cache()
    storage_ok = _check_storage()
    return {
        "status": "ok" if (db and cache_ok and storage_ok) else "degraded",
        "db": db,
        "cache": cache_ok,
        "storage": storage_ok,
    }
