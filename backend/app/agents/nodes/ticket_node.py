from backend.app.agents.tools_registry import TOOLS_REGISTRY

def ticket_node(state):
    service = state.get("service")

    ticket_tool = TOOLS_REGISTRY.get("open_incident_ticket")

    try:
        result = ticket_tool(service=service)
        state["action_results"].append({
            "action": "open_incident_ticket",
            "service": service,
            "status": result.get("status", "unknown"),
            "details": result
        })
    except Exception as e:
        state["action_results"].append({
            "action": "open_incident_ticket",
            "service": service,
            "status": "error",
            "details": str(e)
        })

    return state
