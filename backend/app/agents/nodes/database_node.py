import time
from typing import Any, Dict

def collect_db_facts(state: Dict[str, Any]) -> Dict[str, Any]:
    # Swap for a real check later, e.g. pg_stat_activity + SHOW max_connections.
    # Scenario override lets you test unhealthy branches.
    active = state.get("sim_active_sessions", 42)
    max_conn = 100
    return {"active_sessions": active, "max_connections": max_conn}

def database_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Collects (or simulates) database facts for correlation."""
    try:
        fact = collect_db_facts(state)
        fact["pool_usage_pct"] = round(
            100 * fact["active_sessions"] / fact["max_connections"]
        )
        fact["status"] = "degraded" if fact["pool_usage_pct"] >= 80 else "ok"
        note = f"[DB] pool {fact['pool_usage_pct']}% ({fact['status']})"
    except Exception as e:
        fact = {"status": "unreachable", "error": str(e)}
        note = f"[DB] check failed: {e}"

    fact.update(source="postgres", collected_at=time.time())

    # SAFE: mutate existing lists instead of replacing the whole state
    db_results = state.get("database_results", [])
    db_results.append(fact)
    state["database_results"] = db_results

    observations = state.get("observations", [])
    observations.append(note)
    state["observations"] = observations

    return state
