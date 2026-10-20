import time
import hashlib
import json
from typing import Any, Dict, Optional

DANGEROUS_TOOLS = {
    "restart_service",
    "scale_service",
    "terminate_idle_db_sessions",
    "invalidate_cache",
    "flush_db_pool",
}

def _action_fingerprint(action: Dict[str, Any]) -> str:
    serialized = json.dumps(action, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]

def approval_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Human-in-the-loop approval gate.
    This node does NOT execute actions — it only decides whether
    the planned action is allowed to proceed.
    """

    action = state.get("next_action")
    obs = state.get("observations", []) or []
    knowledge = state.get("knowledge", [])

    # No action → nothing to approve
    if not action:
        knowledge.append({
            "source": "approval",
            "status": "skipped",
            "reason": "no action",
        })
        obs.append("[APPROVAL] No action to approve.")
        state["knowledge"] = knowledge
        state["observations"] = obs
        state["approved_for_remediation"] = False
        return state

    # Malformed action
    if not isinstance(action, dict) or not action.get("tool"):
        knowledge.append({
            "source": "approval",
            "status": "error",
            "reason": "malformed action",
            "raw_action": action,
        })
        obs.append(f"[APPROVAL] Malformed action received, cannot evaluate: {action!r}")
        state["knowledge"] = knowledge
        state["observations"] = obs
        return state

    tool = action["tool"]

    # Safe tools → auto-approve
    if tool not in DANGEROUS_TOOLS:
        knowledge.append({
            "source": "approval",
            "status": "approved",
            "tool": tool,
            "dangerous": False,
            "approved_at": time.time(),
        })
        obs.append(f"[APPROVAL] '{tool}' does not require human approval → auto-approved.")
        state["knowledge"] = knowledge
        state["observations"] = obs
        state["approved_for_remediation"] = True
        return state

    # Dangerous tool → approval must match THIS specific action
    fingerprint = _action_fingerprint(action)
    approval: Optional[Dict[str, Any]] = state.get("remediation_approval")

    approved = (
        isinstance(approval, dict)
        and approval.get("action_fingerprint") == fingerprint
        and approval.get("approved") is True
    )

    if not approved:
        knowledge.append({
            "source": "approval",
            "status": "blocked",
            "tool": tool,
            "dangerous": True,
            "approved": False,
            "action_fingerprint": fingerprint,
        })
        obs.append(
            f"[APPROVAL] '{tool}' requires human approval → remediation blocked "
            f"(awaiting approval for action {fingerprint})."
        )
        state["knowledge"] = knowledge
        state["observations"] = obs
        state["pending_approval_fingerprint"] = fingerprint
        state["approved_for_remediation"] = False
        return state

    # Approved — consume it so it can't be reused
    knowledge.append({
        "source": "approval",
        "status": "approved",
        "tool": tool,
        "dangerous": True,
        "approved": True,
        "approved_at": approval.get("approved_at", time.time()),
        "action_fingerprint": fingerprint,
    })
    obs.append(
        f"[APPROVAL] Human approval matched action {fingerprint} → '{tool}' allowed."
    )

    state["knowledge"] = knowledge
    state["observations"] = obs
    state["approved_for_remediation"] = True
    state["remediation_approval"] = None  # clear so it can't leak

    return state
