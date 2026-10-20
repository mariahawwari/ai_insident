from backend.app.agents.tools_registry import TOOLS_REGISTRY

def diagnostics_node(state):
    service = state.get("service")

    diagnostics_tool = TOOLS_REGISTRY.get("run_diagnostics")

    try:
        result = diagnostics_tool(service=service)
        state["action_results"].append({
            "action": "run_diagnostics",
            "service": service,
            "status": result.get("status", "unknown"),
            "details": result
        })
    except Exception as e:
        state["action_results"].append({
            "action": "run_diagnostics",
            "service": service,
            "status": "error",
            "details": str(e)
        })

    return state
