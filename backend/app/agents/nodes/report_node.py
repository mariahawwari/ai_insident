from typing import Dict, Any, List, Optional, Literal
from datetime import datetime, timezone
from pydantic import BaseModel

ERROR_RATE_THRESHOLD = 0.05
LATENCY_P95_THRESHOLD_MS = 500
MAX_LOG_LINES = 20

ACTIONS_BY_CATEGORY = {
    "pool_exhaustion": "Increase DB connection pool size or optimize query patterns.",
    "latency": "Profile slow endpoints and check upstream dependencies.",
    "retry_storm": "Tune retry policies (backoff, jitter, limits).",
}


class IncidentReport(BaseModel):
    service: str
    status: Literal["incident", "no_incident", "unknown"]
    summary: str
    root_cause: Optional[Dict[str, Any]]
    impact: List[str]
    timeline: List[str]
    recommended_actions: List[str]
    generated_at: str


def build_incident_report(state: Dict[str, Any]) -> IncidentReport:
    service = state.get("service") or "unknown-service"
    root_cause = state.get("root_cause")
    signals = state.get("correlation_signals") or []
    logs = state.get("logs") or []
    metrics = state.get("metrics") or {}
    db_results = state.get("database_results") or []

    impact: List[str] = []
    err = metrics.get("error_rate")
    lat = metrics.get("latency_p95_ms", metrics.get("latency_ms"))
    if err is not None and err > ERROR_RATE_THRESHOLD:
        impact.append(f"Error rate {err:.1%} (threshold {ERROR_RATE_THRESHOLD:.0%}).")
    if lat is not None and lat > LATENCY_P95_THRESHOLD_MS:
        impact.append(f"Latency {lat:.0f} ms (threshold {LATENCY_P95_THRESHOLD_MS} ms).")

    if root_cause is None:
        status = "unknown"
    elif signals or impact:
        status = "incident"
    else:
        status = "no_incident"

    if status == "unknown":
        summary = f"No root-cause analysis available for '{service}'; status cannot be determined."
    elif status == "no_incident":
        summary = f"No anomalous signals or threshold breaches for '{service}'."
    else:
        rc = root_cause
        summary = (
            f"Incident on '{service}'. Hypothesis ({rc.get('confidence', 'low')} confidence): "
            f"{rc.get('hypothesis', 'none')}"
        )

    dated = sorted(
        (str(r["timestamp"]), f"{r['timestamp']} - DB event: {r['event']}")
        for r in db_results if r.get("timestamp") and r.get("event")
    )
    timeline = [text for _, text in dated]
    timeline += [f"(no timestamp) LOG: {l}" for l in logs[:MAX_LOG_LINES]]
    if len(logs) > MAX_LOG_LINES:
        timeline.append(f"... {len(logs) - MAX_LOG_LINES} more log lines omitted")

    actions: List[str] = []
    if root_cause:
        cat = root_cause.get("category")
        if cat in ACTIONS_BY_CATEGORY:
            actions.append(ACTIONS_BY_CATEGORY[cat])
        actions += [f"Verify: {c}" for c in root_cause.get("next_checks", [])]

    return IncidentReport(
        service=service,
        status=status,
        summary=summary,
        root_cause=root_cause,
        impact=impact or ["No threshold breaches in current metrics."],
        timeline=timeline or ["No timeline data available."],
        recommended_actions=actions or ["Further investigation required."],
        generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def report_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a structured incident report and safely update the agent state."""
    report = build_incident_report(state)
    obs = state.get("observations", []) or []

    obs.append(f"[REPORT] {report.status} report generated for {report.service}")

    # SAFE: mutate state instead of replacing it
    state["incident_report"] = report.model_dump()
    state["observations"] = obs

    return state
