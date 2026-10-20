"""
Service-level golden-signal metrics from Prometheus.

This module collects:
- latency (p95, p99)
- error rate
- throughput (requests/sec)
- saturation (queue depth)

All metrics are queried using a `service="<name>"` label.
Missing or invalid data is returned as None.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Tuple

from .prom_query import PrometheusClient, PrometheusError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric name configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricNames:
    """Prometheus metric names (override if your exporters differ)."""
    duration_bucket: str = "http_request_duration_seconds_bucket"
    requests_total: str = "http_requests_total"
    errors_total: str = "http_request_errors_total"
    queue_depth: str = "http_queue_depth"
    service_label: str = "service"


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

@dataclass
class ServiceMetrics:
    """Golden-signal metrics for a single service."""
    service: str
    window: str

    latency_p95: Optional[float] = None
    latency_p99: Optional[float] = None
    error_rate: Optional[float] = None
    throughput_rps: Optional[float] = None
    saturation: Optional[float] = None

    # key → error message
    errors: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape_label(value: str) -> str:
    """Escape a value for safe use inside PromQL label matchers."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def get_service_metrics(
    service_name: str,
    client: Optional[PrometheusClient] = None,
    window: str = "5m",
    names: MetricNames = MetricNames(),
) -> ServiceMetrics:
    """
    Collect golden-signal metrics for a service.

    A failed query only affects its own field.
    Missing data is returned as None.
    """

    client = client or PrometheusClient()
    selector = f'{names.service_label}="{_escape_label(service_name)}"'

    # PromQL queries
    queries: Dict[str, str] = {
        "latency_p95": (
            f"histogram_quantile(0.95, sum by (le) "
            f"(rate({names.duration_bucket}{{{selector}}}[{window}])))"
        ),
        "latency_p99": (
            f"histogram_quantile(0.99, sum by (le) "
            f"(rate({names.duration_bucket}{{{selector}}}[{window}])))"
        ),
        "error_rate": (
            f"sum(rate({names.errors_total}{{{selector}}}[{window}]))"
            f" / sum(rate({names.requests_total}{{{selector}}}[{window}]))"
        ),
        "throughput_rps": (
            f"sum(rate({names.requests_total}{{{selector}}}[{window}]))"
        ),
        "saturation": (
            f"sum({names.queue_depth}{{{selector}}})"
        ),
    }

    metrics = ServiceMetrics(service=service_name, window=window)

    # Worker function for parallel PromQL execution
    def run_query(item: Tuple[str, str]):
        key, promql = item
        try:
            value = client.query_scalar(promql)
            return key, value, None
        except PrometheusError as exc:
            logger.warning("metric %s failed for %s: %s", key, service_name, exc)
            return key, None, str(exc)

    # Execute all queries concurrently
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        for key, value, err in pool.map(run_query, queries.items()):
            setattr(metrics, key, value)
            if err:
                metrics.errors[key] = err

    return metrics
