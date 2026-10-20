from langgraph.graph import StateGraph, END
from .states import IncidentState

# -----------------------------
# Import all nodes
# -----------------------------
from .nodes.intent_node import intent_node
from .nodes.metrics_node import metrics_node
from .nodes.logs_node import logs_node
from .nodes.database_node import database_node
from .nodes.rag_node import rag_node
from .nodes.correlation_node import correlation_node
from .nodes.root_cause_node import root_cause_node
from .nodes.report_node import report_node

from .nodes.restart_node import restart_node
from .nodes.scale_node import scale_node
from .nodes.cache_node import cache_node
from .nodes.ticket_node import ticket_node
from .nodes.simulate_node import simulate_node

from .nodes.diagnostics_node import diagnostics_node
from .nodes.health_check_node import health_check_node
from .nodes.plan_actions_node import plan_actions_node
from .nodes.approval_node import approval_node
from .nodes.escalation_node import escalation_node
from .nodes.execute_action_node import execute_action_node

from .nodes.flush_node import flush_node

# NEW
from .nodes.verification_node import verification_node, route_after_verification


# -----------------------------
# Build workflow
# -----------------------------
def build_workflow():
    workflow = StateGraph(IncidentState)

    # -----------------------------
    # Register nodes
    # -----------------------------
    workflow.add_node("intent", intent_node)

    workflow.add_node("metrics", metrics_node)
    workflow.add_node("logs", logs_node)
    workflow.add_node("database", database_node)
    workflow.add_node("rag", rag_node)
    workflow.add_node("correlation", correlation_node)
    workflow.add_node("root_cause", root_cause_node)
    workflow.add_node("report", report_node)

    workflow.add_node("restart", restart_node)
    workflow.add_node("scale", scale_node)
    workflow.add_node("cache", cache_node)
    workflow.add_node("ticket", ticket_node)
    workflow.add_node("simulate", simulate_node)

    workflow.add_node("diagnostics", diagnostics_node)
    workflow.add_node("health", health_check_node)
    workflow.add_node("plan", plan_actions_node)
    workflow.add_node("approval", approval_node)
    workflow.add_node("escalation", escalation_node)
    workflow.add_node("execute", execute_action_node)

    workflow.add_node("flush", flush_node)

    # NEW
    workflow.add_node("verification", verification_node)

    # -----------------------------
    # Entry point
    # -----------------------------
    workflow.set_entry_point("intent")

    # -----------------------------
    # Conditional routing from intent
    # -----------------------------
    workflow.add_conditional_edges(
        "intent",
        lambda state: state.intent,
        {
            "flush_db_pool": "flush",
            "open_incident_ticket": "ticket",
            "restart_service": "restart",
            "scale_service": "scale",
            "invalidate_cache": "cache",
            "run_diagnostics": "diagnostics",
            "simulate_incident": "simulate",
            "check_metrics": "metrics",
            "search_logs": "logs",
            "check_database": "database",
            "check_pod_health": "health",
            "investigate_incident": "metrics",
        }
    )

    # -----------------------------
    # Analysis pipeline
    # -----------------------------
    workflow.add_edge("metrics", "logs")
    workflow.add_edge("logs", "database")
    workflow.add_edge("database", "rag")
    workflow.add_edge("rag", "correlation")
    workflow.add_edge("correlation", "root_cause")

    # After root cause → go to planning
    workflow.add_edge("root_cause", "plan")

    # -----------------------------
    # Operational pipeline (corrected)
    # -----------------------------
    workflow.add_edge("plan", "approval")
    workflow.add_edge("approval", "execute")

    # After execution → verification
    workflow.add_edge("execute", "verification")

    # Conditional routing after verification
    workflow.add_conditional_edges(
        "verification",
        route_after_verification
    )

    # -----------------------------
    # End edges
    # -----------------------------
    workflow.add_edge("report", END)
    workflow.add_edge("restart", END)
    workflow.add_edge("scale", END)
    workflow.add_edge("cache", END)
    workflow.add_edge("ticket", END)
    workflow.add_edge("simulate", END)
    workflow.add_edge("flush", END)

    return workflow.compile()


# -----------------------------
# Diagnostic runner
# -----------------------------
if __name__ == "__main__":
    app = build_workflow()

    for step in app.stream(
        {"input": "restart payment-api", "service": "payment-api"},
        stream_mode="updates"
    ):
        print(step)
