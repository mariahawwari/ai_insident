import time
from typing import Any, Dict

def escalation_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Decides whether the incident must be escalated to a human.
    This node does NOT fix anything. It only determines escalation.
    """

    root = state.get("root_cause", {})
    correlation = state.get("correlation", {})
    rag = state.get("rag", {})
    approval = state.get("approval", {})
    plan = state.get("next_action")

    reasons = []

    # Rule 1 — Low confidence root cause
    if root.get("confidence") == "low":
        reasons.append("root_cause_low_confidence")

    # Rule 2 — Multiple competing failure modes
    if correlation.get("matched_failure_modes_count", 1) > 1:
        reasons.append("multiple_failure_modes")

    # Rule 3 — RAG returned no knowledge
    if rag.get("hits", 1) == 0:
        reasons.append("rag_no_hits")

    # Rule 4 — Dangerous action blocked by approval
    if approval.get("status") == "blocked":
        reasons.append("dangerous_action_requires_human")

    # Rule 5 — Missing signals
    if state.get("missing_signals"):
        reasons.append("missing_signals")

    # Prepare existing lists
    knowledge = state.get("knowledge", [])
    observations = state.get("observations", [])

    # No escalation needed
    if not reasons:
        knowledge.append({
            "source": "escalation",
            "status": "not_required",
            "timestamp": time.time(),
        })
        observations.append("[ESCALATION] No human escalation required.")

        state["knowledge"] = knowledge
        state["observations"] = observations
        state["escalation_required"] = False
        return state

    # Escalation required
    knowledge.append({
        "source": "escalation",
        "status": "required",
        "reasons": reasons,
        "timestamp": time.time(),
    })
    observations.append(
        f"[ESCALATION] Human escalation required due to: {', '.join(reasons)}."
    )

    state["knowledge"] = knowledge
    state["observations"] = observations
    state["escalation_required"] = True
    return state
