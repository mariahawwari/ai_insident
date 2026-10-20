import time
from datetime import datetime, timezone
from typing import Dict, Any

# Allow ALL services during development
# You can later replace this with a strict allowlist if needed.
COOLDOWN_S = 900
_last_restart: Dict[str, float] = {}  # resets on process restart


def _result(service: str, status: str, details: str) -> Dict[str, Any]:
    return {
        "action": "restart",
        "service": service,
        "status": status,
        "details": details
    }


def restart_service(service: str, dry_run: bool = True) -> Dict[str, Any]:
    # Treat service name as both namespace and deployment for dev/testing
    namespace = "prod"
    deployment = service

    # Cooldown check
    elapsed = time.time() - _last_restart.get(service, 0)
    if elapsed < COOLDOWN_S:
        return _result(
            service,
            "rejected",
            f"Cooldown active, last restart {elapsed:.0f}s ago."
        )

    # Dry-run mode (default)
    if dry_run:
        return _result(
            service,
            "dry_run",
            f"Would rollout-restart deployment/{deployment} in {namespace}."
        )

    # Real Kubernetes restart
    try:
        from kubernetes import client, config
        config.load_kube_config()

        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt":
                                datetime.now(timezone.utc).isoformat()
                        }
                    }
                }
            }
        }

        client.AppsV1Api().patch_namespaced_deployment(
            deployment,
            namespace,
            patch
        )

        _last_restart[service] = time.time()

        return _result(
            service,
            "triggered",
            f"Rollout restart requested for {deployment}. Not yet verified."
        )

    except Exception as e:
        return _result(
            service,
            "error",
            f"{type(e).__name__}: {e}"
        )
