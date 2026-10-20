import time
from contextlib import contextmanager

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Agent runs call LLMs, so they're slow. Runbook search is a fast DB call.
AGENT_BUCKETS = (0.5, 1, 2, 5, 10, 20, 30, 60, 120)
FAST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5)

# ---- Incident investigations ----
INCIDENT_REQUESTS = Counter(
    "incident_agent_requests_total",
    "Total incident investigations",
    ["status"],  # success | error
)
INCIDENT_LATENCY = Histogram(
    "incident_agent_latency_seconds",
    "Latency of incident investigations",
    buckets=AGENT_BUCKETS,
)
INCIDENTS_IN_PROGRESS = Gauge(
    "incident_agent_in_progress",
    "Investigations currently running",
    multiprocess_mode="livesum",
)

# ---- Tools ----
TOOL_CALLS = Counter(
    "incident_agent_tool_calls_total",
    "Tool executions",
    ["tool", "status"],  # success | error
)
TOOL_LATENCY = Histogram(
    "incident_agent_tool_latency_seconds",
    "Tool execution latency",
    ["tool"],
    buckets=AGENT_BUCKETS,
)

# ---- Runbook RAG ----
RUNBOOK_SEARCH_LATENCY = Histogram(
    "incident_agent_runbook_search_latency_seconds",
    "Runbook vector search latency",
    buckets=FAST_BUCKETS,
)
RUNBOOK_SEARCH_EMPTY = Counter(
    "incident_agent_runbook_search_empty_total",
    "Runbook searches that returned no results",
)


@contextmanager
def track_incident():
    INCIDENTS_IN_PROGRESS.inc()
    start = time.perf_counter()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        INCIDENT_LATENCY.observe(time.perf_counter() - start)
        INCIDENT_REQUESTS.labels(status=status).inc()
        INCIDENTS_IN_PROGRESS.dec()


@contextmanager
def track_tool(tool_name: str):
    """Only call with names already validated against TOOLS_REGISTRY."""
    start = time.perf_counter()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        TOOL_LATENCY.labels(tool=tool_name).observe(time.perf_counter() - start)
        TOOL_CALLS.labels(tool=tool_name, status=status).inc()


def get_metrics() -> bytes:
    return generate_latest()


METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST