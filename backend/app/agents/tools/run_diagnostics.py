from typing import Dict, Any, List
from kubernetes import client, config
import os
import redis
import psycopg2
from contextlib import closing
from typing import Optional, List
from app.agents.types.diagnostics import Check

def _section(fn):
    try:
        return {"status": "ok", "data": fn()}
    except Exception as e:
        # Log details elsewhere; state only gets the error type
        return {"status": "error", "error": type(e).__name__}


def _pod_summary(pod) -> Dict[str, Any]:
    cs = pod.status.container_statuses or []
    return {
        "pod": pod.metadata.name,
        "phase": pod.status.phase,
        "ready": all(c.ready for c in cs) if cs else False,
        "restarts": sum(c.restart_count for c in cs),
    }


def collect_k8s(service: str, namespace: str) -> Dict[str, Any]:
    config.load_kube_config()
    core = client.CoreV1Api()
    apps = client.AppsV1Api()

    # Deployment
    dep = apps.read_namespaced_deployment(
        name=service,
        namespace=namespace,
        _request_timeout=10,
    )

    # Selector from deployment spec
    selector = ",".join(
        f"{k}={v}" for k, v in dep.spec.selector.match_labels.items()
    )

    # Pods
    pods = core.list_namespaced_pod(
        namespace,
        label_selector=selector,
        _request_timeout=10,
    ).items

    # ReplicaSets
    rsets = apps.list_namespaced_replica_set(
        namespace,
        label_selector=selector,
        _request_timeout=10,
    ).items

    # Related object names
    related = {p.metadata.name for p in pods} | \
              {r.metadata.name for r in rsets} | \
              {dep.metadata.name}

    # Warning events only
    events = core.list_namespaced_event(
        namespace,
        field_selector="type=Warning",
        _request_timeout=10,
    ).items

    warnings = []
    for e in events:
        name = e.involved_object.name
        if name in related:
            ts = e.last_timestamp or e.event_time
            warnings.append({
                "object": name,
                "reason": e.reason,
                "message": e.message,
                "time": str(ts),
            })
            if len(warnings) >= 15:
                break  # cap for LLM safety

    return {
        "deployment": {
            "replicas": dep.status.replicas,
            "ready_replicas": dep.status.ready_replicas,
            "updated_replicas": dep.status.updated_replicas,
            "available_replicas": dep.status.available_replicas,
        },
        "pods": [_pod_summary(p) for p in pods],
        "warnings": warnings,
    }


def collect_postgres() -> Dict[str, Any]:
    dsn = os.getenv("DB_DSN")
    if not dsn:
        raise RuntimeError("DB_DSN not set")

    conn = psycopg2.connect(dsn, connect_timeout=5)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)

    with closing(conn), closing(conn.cursor()) as cur:
        # Max connections
        cur.execute("SHOW max_connections;")
        max_conn = int(cur.fetchone()[0])

        # Active client sessions
        cur.execute(
            """
            SELECT count(*)
            FROM pg_stat_activity
            WHERE backend_type = 'client backend'
              AND datname = current_database();
            """
        )
        active = cur.fetchone()[0]

        return {
            "max_connections": max_conn,
            "active_sessions": active,
        }


def collect_redis() -> Dict[str, Any]:
    url = os.getenv("REDIS_URL")
    if not url:
        raise RuntimeError("REDIS_URL not set")

    r = redis.Redis.from_url(url, socket_timeout=2)
    info = r.info()

    return {
        "connected_clients": info.get("connected_clients"),
        "used_memory_human": info.get("used_memory_human"),
        "evicted_keys": info.get("evicted_keys"),
        "expired_keys": info.get("expired_keys"),
    }


def run_diagnostics(service: str, namespace: str = "prod") -> Dict[str, Any]:
    """
    Collects raw diagnostic facts from Kubernetes, Postgres, and Redis.
    No thresholds, no verdicts — correlation node interprets these facts.
    """

    return {
        "action": "run_diagnostics",
        "service": service,
        "status": "success",
        "details": {
            "k8s": _section(lambda: collect_k8s(service, namespace)),
            "postgres": _section(collect_postgres),
            "redis": _section(collect_redis),
        },
    }
