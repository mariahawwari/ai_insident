from datetime import datetime, timezone
from typing import Dict, Any, List
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

RECENT_S = 3600   # ignore terminations older than this
TIMEOUT_S = 10


def _load_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def _result(service: str, status: str, details: Any) -> Dict[str, Any]:
    return {"action": "check_pod_health", "service": service, "status": status, "details": details}


def  summarize_pod(pod) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    statuses = pod.status.container_statuses or []
    problems: List[str] = []

    for c in statuses:
        w = c.state.waiting if c.state else None
        if w and w.reason:
            problems.append(f"{c.name}: waiting ({w.reason})")
        t = c.last_state.terminated if c.last_state else None
        if t and t.reason in ("OOMKilled", "Error") and t.finished_at:
            age = int((now - t.finished_at).total_seconds())
            if age < RECENT_S:
                problems.append(f"{c.name}: {t.reason} (exit {t.exit_code}) {age}s ago")

    conds = {cd.type: cd for cd in (pod.status.conditions or [])}
    ready = conds.get("Ready") is not None and conds["Ready"].status == "True"
    sched = conds.get("PodScheduled")
    if sched and sched.status == "False":
        problems.append(f"unschedulable: {sched.reason} - {sched.message}")

    return {
        "pod": pod.metadata.name,
        "phase": pod.status.phase,
        "ready": ready,
        "terminating": pod.metadata.deletion_timestamp is not None,
        "restarts_total": sum(c.restart_count for c in statuses),
        "problems": problems,
    }


def check_pod_health(service: str, namespace: str = "prod") -> Dict[str, Any]:
    try:
        _load_config()
        apps, core = client.AppsV1Api(), client.CoreV1Api()

        try:
            dep = apps.read_namespaced_deployment(service, namespace, _request_timeout=TIMEOUT_S)
        except ApiException as e:
            if e.status == 404:
                return _result(service, "not_found", f"No deployment '{service}' in {namespace}.")
            raise

        desired = dep.spec.replicas or 0
        selector = ",".join(f"{k}={v}" for k, v in dep.spec.selector.match_labels.items())
        pods = core.list_namespaced_pod(namespace, label_selector=selector, _request_timeout=TIMEOUT_S)

        summaries = [_summarize_pod(p) for p in pods.items]
        live = [s for s in summaries if not s["terminating"]]
        ready = sum(s["ready"] for s in live)

        if desired > 0 and ready == 0:
            status = "unhealthy"
        elif ready < desired or any(s["problems"] for s in live):
            status = "degraded"
        else:
            status = "healthy"

        return _result(service, status, {
            "desired": desired,
            "ready": ready,
            "pods": summaries,
        })
    except Exception as e:
        return _result(service, "error", f"{type(e).__name__}: {e}")
    