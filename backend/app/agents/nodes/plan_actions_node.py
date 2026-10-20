from typing import Any, Dict

def plan_actions_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Decides one next action from structured root-cause facts."""
    root = state.get("root_cause") or {}
    category = root.get("category")
    confidence = root.get("confidence")
    service = state.get("service", "")
    obs = state.get("observations", []) or []

    # Approval gate: planner NEVER sets dry_run=False unless explicitly allowed
    live_mode = state.get("approved_for_remediation", False)

    # ---------------------------------------------------------
    # NEW: Capture baseline pod restarts BEFORE any action
    # ---------------------------------------------------------
    metrics = state.get("metrics", {}) or {}
    baseline_restarts = metrics.get("pod_restarts", 0)
    state["baseline_pod_restarts"] = baseline_restarts
    obs.append(f"[PLAN] Baseline pod restarts = {baseline_restarts}")

    def escalate(reason: str) -> Dict[str, Any]:
        obs.append(reason)
        return {
            "tool": "open_incident_ticket",
            "params": {
                "service": service,
                "summary": f"Incident for {service}",
                "description": state.get("report_text", ""),
                "severity": "major",
                "dry_run": False,  # escalation is always real
            },
        }

    # --- Confidence gating ---
    if confidence not in ("high", "medium"):
        next_action = escalate("[PLAN] Confidence insufficient → escalate to humans.")

    # --- DB pool exhaustion ---
    elif category == "db_pool":
        obs.append("[PLAN] Root cause: DB pool exhaustion → terminate idle sessions.")
        next_action = {
            "tool": "terminate_idle_db_sessions",
            "params": {
                "service": service,
                "dry_run": not live_mode,
            },
        }

    # --- Cache stale ---
    elif category == "cache_stale":
        obs.append("[PLAN] Root cause: stale cache → invalidate cache.")
        next_action = {
            "tool": "invalidate_cache",
            "params": {
                "service": service,
                "dry_run": not live_mode,
            },
        }

    # --- Crash ---
    elif category == "k8s_crash":
        obs.append("[PLAN] Root cause: crash → restart service.")
        next_action = {
            "tool": "restart_service",
            "params": {
                "service": service,
                "dry_run": not live_mode,
            },
        }

    # --- Saturation ---
    elif category == "k8s_saturation":
        replicas = state.get("target_replicas")
        if not isinstance(replicas, int) or replicas < 1:
            next_action = escalate(
                "[PLAN] Saturation found but target replicas are invalid → escalate."
            )
        else:
            obs.append("[PLAN] Root cause: saturation → scale service.")
            next_action = {
                "tool": "scale_service",
                "params": {
                    "service": service,
                    "replicas": replicas,
                    "dry_run": not live_mode,
                },
            }

    # --- Unknown category ---
    else:
        next_action = escalate(
            f"[PLAN] Unknown category '{category}' → escalate to humans."
        )

    # SAFE: mutate state instead of replacing it
    state["next_action"] = next_action
    state["observations"] = obs

    # NEW: Tell LangGraph to go to execute_action_node next
    state["next_step"] = "execute_action_node"

    return state
