from typing import Any, Dict

from tools.metrics_pkg.service_metrics import get_service_metrics
from tools.metrics_pkg.k8s_metrics import get_k8s_metrics
from tools.metrics_pkg.db_metrics import get_db_metrics


def metrics_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect service, Kubernetes, and database metrics and update the agent state.
    All metric tools return dataclasses; we convert them to dicts for storage.
    """

    service = state.get("service")
    namespace = state.get("namespace", "default")
    pod = state.get("pod", service)
    database = state.get("database", "postgres")

    # Collect metrics from all three subsystems
    service_m = get_service_metrics(service)
    k8s_m = get_k8s_metrics(namespace, pod)
    db_m = get_db_metrics(database)

    # Convert dataclasses to dicts
    metrics = {
        **service_m.__dict__,
        **k8s_m.__dict__,
        **db_m.__dict__,
    }

    # Append observation
    observations = state.get("observations", [])
    observations.append(f"[METRICS] {service}: {metrics}")

    # Update state
    state["metrics"] = metrics
    state["observations"] = observations

    return state
