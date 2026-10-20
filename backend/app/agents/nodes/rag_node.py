import logging
import time
from typing import Any, Dict

from backend.app.agents.tools_registry import TOOLS_REGISTRY

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 50


def rag_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Runs RAG retrieval and returns structured knowledge facts."""
    service = state.get("service", "")
    logs = state.get("logs", [])[-MAX_LOG_LINES:]

    obs = state.get("observations", []) or []

    fn = TOOLS_REGISTRY.get("rag_retrieval")
    if not callable(fn):
        obs.append("[RAG] rag_retrieval tool not registered.")
        state["knowledge"] = [{"source": "rag", "status": "unavailable"}]
        state["observations"] = obs
        return state

    if not service:
        obs.append("[RAG] Skipped: no service in state.")
        state["knowledge"] = [{"source": "rag", "status": "skipped", "reason": "no service"}]
        state["observations"] = obs
        return state

    try:
        result = fn(service=service, logs=logs)
        hits = len(result) if isinstance(result, (list, tuple)) else 1
        fact = {"source": "rag", "status": "ok", "data": result, "hits": hits}
        note = f"[RAG] Retrieved {hits} knowledge item(s)."
    except Exception as exc:
        logger.exception("rag_retrieval failed")
        fact = {"source": "rag", "status": "error", "error": str(exc)}
        note = f"[RAG] Retrieval failed: {exc}"

    fact["collected_at"] = time.time()

    # SAFE: mutate state instead of replacing it
    state["knowledge"] = [fact]
    obs.append(note)
    state["observations"] = obs

    return state
