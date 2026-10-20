from backend.app.agents.tools_registry import TOOLS_REGISTRY

def simulate_node(state):
    service = state.get("service")

    simulate_tool = TOOLS_REGISTRY.get("simulate_incident")

    try:
        result = simulate_tool(service=service)
        state["action_results"].append({
            "action": "simulate_incident",
            "service": service,
            "status": result.get("status", "unknown"),
            "details": result
        })
    except Exception as e:
        state["action_results"].append({
            "action": "simulate_incident",
            "service": service,
            "status": "error",
            "details": str(e)
        })

    return state
