from backend.app.agents.tools.check_pod_health import check_pod_health
from backend.app.agents.tools.flush_db_pool import terminate_idle_db_sessions
from backend.app.agents.tools.invalidate_cache import invalidate_cache
from backend.app.agents.tools.open_incident_ticket import run_diagnostics
from backend.app.agents.tools.restart_tool import restart_service
from backend.app.agents.tools.scale_service import scale_service
from backend.app.agents.tools.simulate_incident import make_test_state
from backend.app.agents.tools.get_logs import get_logs, get_pod_logs

TOOLS_REGISTRY = {
    "check_pod_health": check_pod_health,
    "terminate_idle_db_sessions": terminate_idle_db_sessions,
    "invalidate_cache": invalidate_cache,
    "restart_service": restart_service,
    "run_diagnostics": run_diagnostics,
    "scale_service": scale_service,
    "simulate_incident": make_test_state,
    "get_logs": get_logs,
    "get_pod_logs": get_pod_logs,
}

# ⭐ Import RAG last — after registry is created
from backend.app.agents.tools.rag_retrieval import rag_retrieval

TOOLS_REGISTRY["rag_retrieval"] = rag_retrieval
