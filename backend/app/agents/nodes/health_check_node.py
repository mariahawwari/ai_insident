from typing import Any, Dict
from backend.app.agents.tools_registry import TOOLS_REGISTRY

def health_check_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Runs the pod-health tool and stores raw observational facts."""
    service = state.get("service", "")
    obs = state.get("observations", []) or []
    results = state.get("action_results", []) or []

    fn = TOOLS_REGISTRY.get("check_pod_health")
    if not fn:
        obs.append("[HEALTH] check_pod_health not found in registry.")
        state["observations"] = obs
        state["action_results"] = results
        return state

    try:
        result = fn(service=service)
        results.append(result)
        obs.append(f"[HEALTH] Pod health collected for {service}.")
    except Exception as e:
        err = {
            "tool": "check_pod_health",
            "status": "error",
            "details": type(e).__name__,
        }
        results.append(err)
        obs.append(f"[HEALTH] Error collecting pod health: {err}")

    # SAFE: mutate state instead of replacing it
    state["observations"] = obs
    state["action_results"] = results
    return state
