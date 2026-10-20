import logging
import os
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Callable, Dict

import psycopg2
import redis
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from backend.app.agents.tools.check_pod_health import summarize_pod


log = logging.getLogger(__name__)

K8S_TIMEOUT_S = 10
MAX_EVENTS = 15

SERVICES = {
    "checkout": {"namespace": "prod", "deployment": "checkout", "db_app": "checkout"},
}


class _Skipped(Exception):
    pass


def _result(service: str, status: str, details: Any) -> Dict[str, Any]:
    return {"action": "run_diagnostics", "service": service, "status": status, "details": details}


def _section(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Each source fails on its own and says so."""
    try:
        return {"status": "ok", "data": fn()}
    except _Skipped as e:
        return {"status": "skipped", "reason": str(e)}
    except ApiException as e:
        log.warning("k8s section failed: %s %s", e.status, e.reason)
        return {"status": "error", "error": f"Kubernetes API {e.status} {e.reason}"}
    except Exception as e:
        log.exception("diagnostics section failed")  # full detail in logs, only the type goes to state
        return {"status": "error", "error": type(e).__name__}


def _load_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _event_time(e):
    return e.last_timestamp or e.event_time or e.metadata.creation_timestamp


def _collect_k8s(cfg: Dict[str, str]) -> Dict[str, Any]:
    ns, name, t = cfg["namespace"], cfg["deployment"], K8S_TIMEOUT_S
    _load_config()
    apps, core = client.AppsV1Api(), client.CoreV1Api()

    dep = apps.read_namespaced_deployment(name, ns, _request_timeout=t)
    selector = ",".join(f"{k}={v}" for k, v in dep.spec.selector.match_labels.items())
    pods = core.list_namespaced_pod(ns, label_selector=selector, _request_timeout=t).items
    rsets = apps.list_namespaced_replica_set(ns, label_selector=selector, _request_timeout=t).items

    # Events attach to pods, ReplicaSets and the Deployment, never to a bare "checkout" pod
    related = {p.metadata.name for p in pods} | {r.metadata.name for r in rsets} | {name}
    events = core.list_namespaced_event(ns, field_selector="type=Warning", _request_timeout=t).items
    recent = sorted((e for e in events if e.involved_object.name in related),
                    key=_event_time, reverse=True)[:MAX_EVENTS]

    st = dep.status
    return {
        "deployment": {
            "desired": dep.spec.replicas or 0,
            "ready": st.ready_replicas or 0,
            "updated": st.updated_replicas or 0,
            "available": st.available_replicas or 0,
            "conditions": [
                {"type": c.type, "status": c.status, "reason": c.reason,
                 "message": (c.message or "")[:200]}
                for c in (st.conditions or [])
            ],
        },
        "pods": [summarize_pod(p) for p in pods],
        "warning_events": [
            {"object": f"{e.involved_object.kind}/{e.involved_object.name}",
             "reason": e.reason, "message": (e.message or "")[:300],
             "count": e.count, "last_seen": _event_time(e).isoformat()}
            for e in recent
        ],
    }


def _collect_redis() -> Dict[str, Any]:
    url = os.getenv("REDIS_RO_URL")
    if not url:
        raise _Skipped("REDIS_RO_URL not set")
    with closing(redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)) as r:
        info = r.info()
    used, maxmem = info.get("used_memory", 0), info.get("maxmemory", 0)
    return {
        "used_memory_bytes": used,
        "maxmemory_bytes": maxmem,  # 0 = no limit
        "memory_pct": round(100 * used / maxmem, 1) if maxmem else None,
        "connected_clients": info.get("connected_clients"),
        "blocked_clients": info.get("blocked_clients"),
        "uptime_s": info.get("uptime_in_seconds"),  # low = recent restart, counters reset
        # CUMULATIVE since server start: diff against a previous snapshot
        "evicted_keys": info.get("evicted_keys"),
        "rejected_connections": info.get("rejected_connections"),
        "keyspace_hits": info.get("keyspace_hits"),
        "keyspace_misses": info.get("keyspace_misses"),
    }


_PG_SQL = """
SELECT (application_name = %s)                                       AS mine,
       coalesce(state, 'unknown')                                    AS state,
       count(*)                                                      AS n,
       count(*) FILTER (WHERE wait_event_type = 'Lock')              AS lock_waiters,
       coalesce(max(extract(epoch FROM now() - xact_start)), 0)::int AS oldest_xact_s
FROM pg_stat_activity
WHERE datname = current_database()
  AND backend_type = 'client backend'
  AND pid <> pg_backend_pid()
GROUP BY 1, 2
"""


def _collect_postgres(app_name: str) -> Dict[str, Any]:
    dsn = os.getenv("DB_RO_DSN")
    if not dsn:
        raise _Skipped("DB_RO_DSN not set")
    with closing(psycopg2.connect(dsn, connect_timeout=3,
                                  options="-c statement_timeout=3000")) as conn:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SHOW max_connections")
            max_conn = int(cur.fetchone()[0])
            cur.execute(_PG_SQL, (app_name,))
            rows = cur.fetchall()
    total = sum(r[2] for r in rows)
    return {
        "max_connections": max_conn,
        "total_connections": total,
        "utilization_pct": round(100 * total / max_conn, 1),
        "service_sessions": {r[1]: {"count": r[2], "oldest_xact_s": r[4]} for r in rows if r[0]},
        "lock_waiters": sum(r[3] for r in rows),
    }


def run_diagnostics(service: str) -> Dict[str, Any]:
    cfg = SERVICES.get(service)
    if cfg is None:
        return _result(service, "rejected", "Service not in registry.")

    sections = {
        "kubernetes": _section(lambda: _collect_k8s(cfg)),
        "redis": _section(_collect_redis),
        "postgres": _section(lambda: _collect_postgres(cfg["db_app"])),
    }
    ran = [s for s in sections.values() if s["status"] != "skipped"]
    failed = [s for s in ran if s["status"] == "error"]
    status = "failed" if not ran or len(failed) == len(ran) else "partial" if failed else "complete"

    return _result(service, status, {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
    })