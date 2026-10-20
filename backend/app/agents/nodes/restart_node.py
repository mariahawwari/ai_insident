from backend.app.agents.tools_registry import TOOLS_REGISTRY

def restart_node(state):
    # Pydantic access
    service = state.service

    if not service:
        state.action_results.append({
            "action": "restart_service",
            "status": "error",
            "details": "No service provided."
        })
        return state

    restart_tool = TOOLS_REGISTRY.get("restart_service")

    try:
        # Execute tool
        result = restart_tool(service=service)

        # Append results using Pydantic-safe access
        state.action_results.append({
            "action": "restart_service",
            "service": service,
            "status": result.get("status", "unknown"),
            "details": result
        })

    except Exception as e:
        state.action_results.append({
            "action": "restart_service",
            "service": service,
            "status": "error",
            "details": str(e)
        })

    return state
