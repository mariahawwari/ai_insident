import os
import time
from typing import Dict, Any, List

import redis

# service -> prefixes holding ONLY rebuildable cache data (no sessions, carts, user data)
CACHE_PREFIXES = {
    "checkout": ["cache:checkout:"],
}

MAX_KEYS = 500   # hard cap per call, not caller-controlled
BATCH = 100
COOLDOWN_S = 600
_last_invalidate: Dict[str, float] = {}  # in-memory, resets on process restart


def _result(service: str, status: str, details: Any, **extra) -> Dict[str, Any]:
    return {"action": "invalidate_cache", "service": service,
            "status": status, "details": details, **extra}


def invalidate_cache(service: str, dry_run: bool = True) -> Dict[str, Any]:
    prefixes = CACHE_PREFIXES.get(service)
    if not prefixes:
        return _result(service, "rejected", "Service not in cache prefix allowlist.")

    elapsed = time.time() - _last_invalidate.get(service, 0)
    if elapsed < COOLDOWN_S:
        return _result(service, "rejected", f"Cooldown active, last invalidation {elapsed:.0f}s ago.")

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return _result(service, "error", "REDIS_URL not set in environment.")

    deleted = 0
    try:
        r = redis.Redis.from_url(redis_url, socket_timeout=2, socket_connect_timeout=2)

        keys: List[bytes] = []
        by_prefix: Dict[str, int] = {}
        truncated = False
        for prefix in prefixes:
            n = 0
            for key in r.scan_iter(match=f"{prefix}*", count=500):
                if len(keys) >= MAX_KEYS:
                    truncated = True
                    break
                keys.append(key)
                n += 1
            by_prefix[prefix] = n
            if truncated:
                break

        summary = {"count": len(keys), "by_prefix": by_prefix, "truncated": truncated}

        if dry_run:
            return _result(service, "dry_run", summary)

        for i in range(0, len(keys), BATCH):
            deleted += r.unlink(*keys[i:i + BATCH])

        _last_invalidate[service] = time.time()
        return _result(service, "triggered", {**summary, "deleted": deleted},
                       note="Not yet verified.")
    except Exception as e:
        if deleted:
            _last_invalidate[service] = time.time()  # an action did happen
        return _result(service, "error", f"{type(e).__name__}: {e}", deleted_before_error=deleted)