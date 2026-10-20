# backend/app/agents/nodes/logs_node.py

import logging
from typing import Any, Dict, List

from backend.app.agents.tools_registry import TOOLS_REGISTRY

logger = logging.getLogger(__name__)

# get_pod_logs reports its own failures as lines starting with these prefixes
_TOOL_FAILURE_PREFIXES = ("[ERROR] get_pod_logs:", "[WARN] get_pod_logs:")


def _is_tool_failure(lines: List[str]) -> bool:
    """True when get_pod_logs returned only its own failure message(s), i.e. no real evidence."""
    return bool(lines) and all(line.startswith(_TOOL_FAILURE_PREFIXES) for line in lines)


def logs_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect recent logs for the target pod via the get_pod_logs tool.

    Writes state["logs"] (list of lines, empty on any failure) and appends one
    observation describing what actually happened: collected, empty, or failed.
    """
    namespace = state.get("namespace", "default")
    pod = state.get("pod")

    obs: List[str] = list(state.get("observations") or [])  # copy, don't mutate state in place

    def done(logs: List[str], note: str) -> Dict[str, Any]:
        obs.append(note)
        return {**state, "logs": logs, "observations": obs}

    if not pod or not isinstance(pod, str):
        return done([], "[LOGS] No target pod in state; skipped log collection.")

    fn = TOOLS_REGISTRY.get("get_pod_logs")
    if not callable(fn):
        logger.error("get_pod_logs not found in TOOLS_REGISTRY")
        return done([], "[LOGS] get_pod_logs tool not found in registry.")

    try:
        logs = fn(namespace=namespace, pod=pod)
    except Exception as exc:
        logger.exception("get_pod_logs failed for pod=%s", pod)
        return done([], f"[LOGS] Error collecting logs for {pod}: {type(exc).__name__}: {exc}")

    if not isinstance(logs, list):
        logger.error("get_pod_logs returned %s, expected list", type(logs).__name__)
        return done([], f"[LOGS] get_pod_logs returned unexpected type for {pod}.")

    if not logs:
        return done([], f"[LOGS] No log lines returned for {pod}.")

    if _is_tool_failure(logs):
        return done([], f"[LOGS] Collection failed for {pod}: {logs[0]}")

    return done(logs, f"[LOGS] Collected {len(logs)} lines for {pod}.")
