from backend.app.agents.tools_registry import TOOLS_REGISTRY

def scale_node(state):
    service = state.get("service")
    replicas = state.get("replicas", None)

    if not service:
        state["action_results"].append({
            "action": "scale_service",
            "status": "error",
            "details": "No service provided."
        })
        return state

    scale_tool = TOOLS_REGISTRY.get("scale_service")

    try:
        result = scale_tool(service=service, replicas=replicas)
        state["action_results"].append({
            "action": "scale_service",
            "service": service,
            "status": result.get("status", "unknown"),
            "details": result
        })
    except Exception as e:
        state["action_results"].append({
            "action": "scale_service",
            "service": service,
            "status": "error",
            "details": str(e)
        })

    return state
