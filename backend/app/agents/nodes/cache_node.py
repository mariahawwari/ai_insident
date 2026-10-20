from backend.app.agents.tools_registry import TOOLS_REGISTRY

def cache_node(state):
    service = state.get("service")

    if not service:
        state["action_results"].append({
            "action": "invalidate_cache",
            "status": "error",
            "details": "No service provided."
        })
        return state

    cache_tool = TOOLS_REGISTRY.get("invalidate_cache")

    try:
        result = cache_tool(service=service)
        state["action_results"].append({
            "action": "invalidate_cache",
            "service": service,
            "status": result.get("status", "unknown"),
            "details": result
        })
    except Exception as e:
        state["action_results"].append({
            "action": "invalidate_cache",
            "service": service,
            "status": "error",
            "details": str(e)
        })

    return state
