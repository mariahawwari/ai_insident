from backend.app.agents.tools_registry import TOOLS_REGISTRY

def flush_node(state):
    service = state.get("service")
    tool = TOOLS_REGISTRY.get("flush_db_pool")

    if not tool:
        state["action_results"].append({
            "action": "flush_db_pool",
            "service": service,
            "status": "error",
            "details": "flush_db_pool tool not found"
        })
        return state

    try:
        result = tool(service=service)
        state["action_results"].append({
            "action": "flush_db_pool",
            "service": service,
            "status": "ok",
            "details": result
        })
    except Exception as e:
        state["action_results"].append({
            "action": "flush_db_pool",
            "service": service,
            "status": "error",
            "details": str(e)
        })

    return state
