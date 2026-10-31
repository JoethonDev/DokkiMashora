"""Read-only operational status for General Settings.

Everything here is bounded: Redis clients use short socket timeouts, Celery
inspection uses a short reply timeout, and the page degrades to an error string
instead of blocking. No Redis write/flush command is exposed outside the
explicit key helpers used by the settings view.
"""

from __future__ import annotations

import platform
from urllib.parse import urlparse

import celery
import django
import redis
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils.translation import gettext as _

from config.celery import app as celery_app

_CELERY_CACHE_KEY = "system-status:celery:v1"
_REDIS_TIMEOUT = 2.0


def app_version() -> str:
    return str(getattr(settings, "APP_VERSION", "dev") or "dev")


def _redis_url(value: str) -> str:
    return str(value or "").strip()


def redis_url_info(url: str) -> dict[str, object]:
    parsed = urlparse(_redis_url(url))
    if parsed.scheme not in {"redis", "rediss"}:
        return {"configured": False, "secure": False, "host": "", "port": 6379, "database": 0}
    database = 0
    if parsed.path and parsed.path.strip("/"):
        try:
            database = int(parsed.path.strip("/").split("/")[0])
        except (TypeError, ValueError):
            database = 0
    return {
        "configured": True,
        "secure": parsed.scheme == "rediss",
        "host": parsed.hostname or "",
        "port": parsed.port or 6379,
        "database": database,
    }


def mask_redis_url(url: str) -> str:
    info = redis_url_info(url)
    if not info["configured"]:
        return ""
    return f"{info['host']}:{info['port']}/{info['database']}"


def get_redis_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        _redis_url(url),
        decode_responses=True,
        socket_timeout=_REDIS_TIMEOUT,
        socket_connect_timeout=_REDIS_TIMEOUT,
    )


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _redis_overview(url: str, *, label: str, read_only: bool) -> dict[str, object]:
    info = redis_url_info(url)
    payload: dict[str, object] = {
        "label": label,
        "configured": info["configured"],
        "url": mask_redis_url(url),
        "database": info["database"],
        "read_only": read_only,
        "ok": False,
        "error": "",
        "version": "",
        "uptime": "",
        "used_memory": "",
        "maxmemory": "",
        "policy": "",
        "clients": "",
        "keys": 0,
    }
    if not info["configured"]:
        return payload
    client = get_redis_client(url)
    try:
        pipe = client.pipeline(transaction=False)
        pipe.info("server")
        pipe.info("memory")
        pipe.info("clients")
        pipe.dbsize()
        server_raw, memory_raw, clients_raw, db_size = pipe.execute()
        server = {_decode(key): _decode(value) for key, value in server_raw.items()}
        memory = {_decode(key): _decode(value) for key, value in memory_raw.items()}
        clients = {_decode(key): _decode(value) for key, value in clients_raw.items()}
        payload.update(
            {
                "ok": True,
                "version": server.get("redis_version", ""),
                "uptime": server.get("uptime_in_seconds", ""),
                "used_memory": memory.get("used_memory_human", ""),
                "maxmemory": memory.get("maxmemory_human", ""),
                "policy": memory.get("maxmemory_policy", ""),
                "clients": clients.get("connected_clients", ""),
                "keys": int(db_size or 0),
            }
        )
    except Exception as exc:  # noqa: BLE001
        payload["error"] = str(exc)[:300]
    return payload


def get_redis_overview() -> dict[str, object]:
    cache_url = _redis_url(getattr(settings, "CACHE_URL", ""))
    broker_url = _redis_url(getattr(settings, "CELERY_BROKER_URL", ""))
    broker_is_redis = redis_url_info(broker_url)["configured"]
    return {
        "cache": _redis_overview(cache_url, label=_("Cache"), read_only=False),
        "broker": _redis_overview(
            broker_url if broker_is_redis else "",
            label=_("Celery broker"),
            read_only=True,
        ),
        "cache_url": mask_redis_url(cache_url),
        "broker_url": mask_redis_url(broker_url) if broker_is_redis else "",
    }


def get_database_version() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version()")
            row = cursor.fetchone()
        text = str(row[0]) if row else ""
        # "PostgreSQL 18.0 on x86_64..." -> "PostgreSQL 18.0"
        return " ".join(text.split()[:2]) if text else ""
    except Exception:  # noqa: BLE001
        return ""


def get_versions() -> list[dict[str, str]]:
    redis_overview = get_redis_overview()
    redis_version = ""
    if redis_overview["cache"]["ok"]:
        redis_version = str(redis_overview["cache"]["version"])
    elif redis_overview["broker"]["ok"]:
        redis_version = str(redis_overview["broker"]["version"])
    return [
        {"label": _("Application"), "value": app_version()},
        {"label": _("Django"), "value": django.get_version()},
        {"label": _("Python"), "value": platform.python_version()},
        {"label": _("Celery"), "value": celery.__version__},
        {"label": _("Redis"), "value": redis_version or "—"},
        {"label": _("PostgreSQL"), "value": get_database_version() or "—"},
        {"label": _("Platform"), "value": platform.platform()},
    ]


def _celery_inspect_payload() -> dict[str, object]:
    inspect = celery_app.control.inspect(timeout=_REDIS_TIMEOUT)
    payload: dict[str, object] = {
        "ok": False,
        "error": "",
        "app_version": celery.__version__,
        "workers": [],
        "queues": [],
        "active": 0,
        "reserved": 0,
        "scheduled": 0,
    }
    try:
        stats = inspect.stats() or {}
        payload["ok"] = bool(stats)
        workers = []
        for name, worker_stats in stats.items():
            pool = worker_stats.get("pool") or {}
            worker = {
                "name": name,
                "status": "online",
                "uptime": worker_stats.get("uptime", ""),
                "concurrency": pool.get("max-concurrency", ""),
                "pool": pool.get("implementation", ""),
                "total": sum((worker_stats.get("total") or {}).values()),
            }
            workers.append(worker)
        payload["workers"] = sorted(workers, key=lambda item: item["name"])
    except Exception as exc:  # noqa: BLE001
        payload["error"] = str(exc)[:300]
    queues = {settings.CELERY_TASK_DEFAULT_QUEUE}
    for route in (settings.CELERY_TASK_ROUTES or {}).values():
        queue = route.get("queue") if isinstance(route, dict) else None
        if queue:
            queues.add(queue)
    payload["queues"] = sorted(queues)
    return payload


def get_celery_status() -> dict[str, object]:
    try:
        cached = cache.get(_CELERY_CACHE_KEY)
    except Exception:  # noqa: BLE001
        cached = None
    if cached:
        return cached
    payload = _celery_inspect_payload()
    try:
        cache.set(_CELERY_CACHE_KEY, payload, timeout=15)
    except Exception:  # noqa: BLE001
        pass
    return payload


def get_queue_depths(queues: list[str]) -> dict[str, object]:
    broker_url = _redis_url(getattr(settings, "CELERY_BROKER_URL", ""))
    if not redis_url_info(broker_url)["configured"]:
        return {"ok": False, "error": _("Celery broker is not Redis."), "depths": {}}
    client = get_redis_client(broker_url)
    depths: dict[str, int] = {}
    try:
        for queue in queues:
            try:
                depths[queue] = int(client.llen(queue))
            except Exception:  # noqa: BLE001
                depths[queue] = 0
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300], "depths": depths}
    return {"ok": True, "error": "", "depths": depths}


def get_beat_schedule() -> list[dict[str, str]]:
    schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {}) or {}
    rows = []
    for name, entry in schedule.items():
        seconds = entry.get("schedule")
        if isinstance(seconds, (int, float)):
            schedule_text = f"{int(seconds)}s"
        else:
            schedule_text = str(seconds)
        rows.append(
            {
                "name": name,
                "task": entry.get("task", ""),
                "schedule": schedule_text,
            }
        )
    return rows


def is_protected_redis_key(key: str) -> bool:
    normalized = str(key or "")
    if normalized.startswith("assessment-attempts:pending-snapshot"):
        return True
    if normalized.startswith("_kombu."):
        return True
    if normalized.startswith("assessment-attempt:") and (
        ":take:" in normalized or ":snapshot-questions:" in normalized
    ):
        return True
    if normalized.endswith((":generation", ":lock", ":scheduled", ":closed")):
        return True
    return "pending-snapshot" in normalized


def scan_cache_keys(*, pattern: str = "", cursor: int = 0, count: int = 50) -> dict[str, object]:
    cache_url = _redis_url(getattr(settings, "CACHE_URL", ""))
    if not redis_url_info(cache_url)["configured"]:
        return {
            "configured": False,
            "keys": [],
            "next_cursor": 0,
            "db_size": 0,
            "error": _("Django cache is not configured."),
        }
    client = get_redis_client(cache_url)
    keys: list[dict[str, object]] = []
    try:
        scan_cursor, raw_keys = client.scan(
            cursor=max(int(cursor or 0), 0),
            match=pattern or "*",
            count=max(10, min(int(count or 50), 200)),
        )
        for key in raw_keys[:200]:
            key_type = client.type(key)
            ttl = client.ttl(key)
            length = 0
            try:
                if key_type == "string":
                    length = client.strlen(key)
                elif key_type == "list":
                    length = client.llen(key)
                elif key_type == "hash":
                    length = client.hlen(key)
                elif key_type == "set":
                    length = client.scard(key)
                elif key_type == "zset":
                    length = client.zcard(key)
            except Exception:  # noqa: BLE001
                length = 0
            keys.append(
                {
                    "key": key,
                    "type": key_type,
                    "ttl": ttl,
                    "length": length,
                    "protected": is_protected_redis_key(key),
                }
            )
        return {
            "configured": True,
            "keys": keys,
            "next_cursor": scan_cursor,
            "db_size": client.dbsize(),
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "configured": True,
            "keys": [],
            "next_cursor": 0,
            "db_size": 0,
            "error": str(exc)[:300],
        }


def get_cache_key_value(key: str, *, limit: int = 4000) -> dict[str, object]:
    cache_url = _redis_url(getattr(settings, "CACHE_URL", ""))
    if not redis_url_info(cache_url)["configured"] or not key:
        return {"ok": False, "error": _("Cache is not configured."), "type": "", "value": ""}
    client = get_redis_client(cache_url)
    try:
        key_type = client.type(key)
        value = ""
        if key_type == "string":
            size = int(client.strlen(key) or 0)
            if size > limit:
                value = client.getrange(key, 0, limit - 1) or ""
                value = f"{value}\n… ({size} bytes total)"
            else:
                value = client.get(key) or ""
        elif key_type == "list":
            value = "\n".join(str(item) for item in client.lrange(key, 0, 20))
        elif key_type == "hash":
            _, values = client.hscan(key, cursor=0, count=20)
            value = "\n".join(f"{field}: {item}" for field, item in list(values.items())[:20])
        elif key_type == "set":
            _, values = client.sscan(key, cursor=0, count=20)
            value = "\n".join(str(item) for item in list(values)[:20])
        elif key_type == "zset":
            value = "\n".join(
                f"{item}: {score}" for item, score in client.zrange(key, 0, 20, withscores=True)
            )
        return {"ok": True, "error": "", "type": key_type, "value": str(value)[:limit]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300], "type": "", "value": ""}


def set_cache_key(key: str, value: str, ttl: int = 0) -> tuple[bool, str]:
    cache_url = _redis_url(getattr(settings, "CACHE_URL", ""))
    if not redis_url_info(cache_url)["configured"]:
        return False, _("Cache is not configured.")
    if not key or len(key) > 512:
        return False, _("Enter a key up to 512 characters.")
    if len(value) > 100_000:
        return False, _("The value is too large (maximum 100 KB).")
    client = get_redis_client(cache_url)
    try:
        client.set(key, value, ex=int(ttl) if ttl and int(ttl) > 0 else None)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:300]
    return True, ""


def delete_cache_key(key: str) -> tuple[bool, str]:
    cache_url = _redis_url(getattr(settings, "CACHE_URL", ""))
    if not redis_url_info(cache_url)["configured"]:
        return False, _("Cache is not configured.")
    if not key:
        return False, _("A key is required.")
    client = get_redis_client(cache_url)
    try:
        client.delete(key)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:300]
    return True, ""
