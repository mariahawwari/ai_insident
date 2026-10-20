# backend/app/agents/simulate_incident.py

from backend.app.agents.states import IncidentState


def make_test_state() -> IncidentState:
    return IncidentState(
        input="incident simulation",
        service="payment-api",

        # Synthetic metrics
        metrics={
            "error_rate": 0.42,
            "latency_p99_ms": 1800,
            "db_pool_usage_pct": 95,
        },

        # Synthetic logs
        logs=[
            "ERROR: DB pool exhausted for payment-api",
            "WARN: cache miss storm detected",
        ],

        # Optional database results
        database_results=[],

        # RAG results
        knowledge=[],

        # Correlation + root cause will be filled by nodes
        correlation_signals=[],
        root_cause=None,

        # Operational fields
        observations=[],
        action_results=[],
        next_action=None,
        approved_for_remediation=False,

        # For scaling tests
        target_replicas=4,
    )


def main():
    workflow = build_workflow()
    state = make_test_state()

    final_state = workflow.invoke(state)

    print("\n=== Observations ===")
    for o in final_state["observations"]:
        print(o)

    print("\n=== Action Results ===")
    for r in final_state["action_results"]:
        print(r)

    print("\n=== Next Action ===")
    print(final_state.get("next_action"))


if __name__ == "__main__":
    main()
