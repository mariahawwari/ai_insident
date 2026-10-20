import time
from typing import Dict, Any
from kubernetes import client, config

# service -> (namespace, deployment, min_replicas, max_replicas)
SCALABLE = {
    "checkout": ("prod", "checkout", 2, 10),
}
MAX_STEP = 3
COOLDOWN_S = 600
_last_scale: Dict[str, float] = {}  # in-memory, resets on process restart


def _result(service: str, status: str, details: str, **extra) -> Dict[str, Any]:
    return {"action": "scale", "service": service, "status": status, "details": details, **extra}


def _load_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def scale_service(service: str, replicas: int, dry_run: bool = True) -> Dict[str, Any]:
    target = SCALABLE.get(service)
    if target is None:
        return _result(service, "rejected", "Service is not in the scale allowlist.")
    namespace, deployment, lo, hi = target

    if isinstance(replicas, bool) or not isinstance(replicas, int):
        return _result(service, "rejected", f"replicas must be an int, got {type(replicas).__name__}.")
    if not lo <= replicas <= hi:
        return _result(service, "rejected", f"{replicas} is outside allowed range {lo}-{hi}.")

    elapsed = time.time() - _last_scale.get(service, 0)
    if elapsed < COOLDOWN_S:
        return _result(service, "rejected", f"Cooldown active, last scale {elapsed:.0f}s ago.")

    if dry_run:
        return _result(service, "dry_run",
                       f"Would scale deployment/{deployment} in {namespace} to {replicas} replicas.")

    try:
        _load_config()
        apps = client.AppsV1Api()

        current = apps.read_namespaced_deployment_scale(deployment, namespace).spec.replicas
        if current == replicas:
            return _result(service, "no_op", f"Already at {replicas} replicas.")
        if abs(replicas - current) > MAX_STEP:
            return _result(service, "rejected",
                           f"Change {current} -> {replicas} exceeds max step of {MAX_STEP}.")

        hpas = client.AutoscalingV2Api().list_namespaced_horizontal_pod_autoscaler(namespace).items
        if any(h.spec.scale_target_ref.kind == "Deployment"
               and h.spec.scale_target_ref.name == deployment for h in hpas):
            return _result(service, "rejected", "Deployment is managed by an HPA; adjust the HPA instead.")

        apps.patch_namespaced_deployment_scale(
            name=deployment, namespace=namespace, body={"spec": {"replicas": replicas}}
        )
        _last_scale[service] = time.time()
        return _result(service, "triggered",
                       f"Scaling {deployment}: {current} -> {replicas}. Not yet verified.",
                       previous_replicas=current, requested_replicas=replicas)
    except Exception as e:
        return _result(service, "error", f"{type(e).__name__}: {e}")