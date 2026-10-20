from typing import Any, Dict
from backend.app.agents.tools_registry import TOOLS_REGISTRY

# Executor allowlist — prevents planner tampering
EXECUTOR_ALLOWLIST = {
    "restart_service",
    "scale_service",
    "check_pod_health",
    "terminate_idle_db_sessions",
    "invalidate_cache",
    "run_diagnostics",
    "open_incident_ticket",
}

def execute_action_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Executes exactly one planned action using the fixed tool registry."""
    action = state.get("next_action")
    obs = state.get("observations", []) or []
    results = state.get("action_results", []) or []

    # No action planned
    if not action:
        obs.append("[EXEC] No action planned.")
        state["observations"] = obs
        state["action_results"] = results
        return state

    # Validate action structure
    if not isinstance(action, dict):
        err = {"tool": None, "status": "error", "details": "InvalidAction"}
        results.append(err)
        obs.append("[EXEC] Planned action has an invalid format.")
        state["observations"] = obs
        state["action_results"] = results
        return state

    tool_name = action.get("tool")
    params = action.get("params", {})

    # Validate tool name
    if not isinstance(tool_name, str) or not tool_name:
        err = {"tool": tool_name, "status": "error", "details": "InvalidToolName"}
        results.append(err)
        obs.append("[EXEC] Planned action has no valid tool name.")
        state["observations"] = obs
        state["action_results"] = results
        return state

    # Enforce executor allowlist
    if tool_name not in EXECUTOR_ALLOWLIST:
        err = {"tool": tool_name, "status": "error", "details": "ToolNotAllowed"}
        results.append(err)
        obs.append(f"[EXEC] Tool '{tool_name}' is not allowed for execution.")
        state["observations"] = obs
        state["action_results"] = results
        return state

    # Validate params
    if not isinstance(params, dict):
        err = {"tool": tool_name, "status": "error", "details": "InvalidParams"}
        results.append(err)
        obs.append(f"[EXEC] Invalid parameters for {tool_name}.")
        state["observations"] = obs
        state["action_results"] = results
        return state

    # Lookup tool
    fn = TOOLS_REGISTRY.get(tool_name)
    if not callable(fn):
        err = {"tool": tool_name, "status": "error", "details": "ToolNotFound"}
        results.append(err)
        obs.append(f"[EXEC] Tool '{tool_name}' not found in registry.")
        state["observations"] = obs
        state["action_results"] = results
        return state

    # Execute tool
    try:
        result = fn(**params)
        results.append(result)
        obs.append(f"[EXEC] {tool_name} executed.")
    except Exception as exc:
        err = {
            "tool": tool_name,
            "status": "error",
            "details": type(exc).__name__,
        }
        results.append(err)
        obs.append(f"[EXEC] Error executing {tool_name}: {err}")

    # SAFE: mutate state instead of replacing it
    state["observations"] = obs
    state["action_results"] = results
    return state
