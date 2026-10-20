from typing import Any, Dict

REMEDIATION_REQUIRE_APPROVAL = {
    "restart_service",
    "scale_service",
    "terminate_idle_db_sessions",
    "invalidate_cache",
}

def approval_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Enforces human approval before any production-changing action."""
    action = state.get("next_action")
    obs = state.get("observations", []) or []

    # No action planned → nothing to approve
    if not action or not isinstance(action, dict):
        obs.append("[APPROVAL] No valid action to approve.")
        state["approved_for_remediation"] = False
        state["observations"] = obs
        return state

    tool_name = action.get("tool")

    # If the tool is not remediation, auto-approve
    if tool_name not in REMEDIATION_REQUIRE_APPROVAL:
        obs.append(f"[APPROVAL] '{tool_name}' does not require approval → auto-approved.")
        state["approved_for_remediation"] = True
        state["observations"] = obs
        return state

    # If user already approved earlier
    if state.get("approved_for_remediation") is True:
        obs.append(f"[APPROVAL] Already approved for '{tool_name}'.")
        state["approved_for_remediation"] = True
        state["observations"] = obs
        return state

    # Otherwise: block and request approval
    obs.append(
        f"[APPROVAL] '{tool_name}' requires human approval → remediation blocked."
    )

    state["approved_for_remediation"] = False
    state["observations"] = obs
    return state
