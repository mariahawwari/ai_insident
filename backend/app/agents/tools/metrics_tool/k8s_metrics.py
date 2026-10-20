"""
Kubernetes pod-level metrics via Prometheus (cAdvisor + kube-state-metrics).

Semantics
---------
* None  -> no data (series missing, exporter down, or query failed)
* 0     -> real measured zero

Correctness notes
-----------------
* cAdvisor exports a pod-level cgroup series (container="") and the pause
  container (container="POD"). Both must be excluded to avoid double counting.
* Node pressure is computed per-node using kube_node_status_condition joined
  with kube_pod_info. It is NOT a cluster-wide sum.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Tuple

from .prom_query import PrometheusClient, PrometheusError

logger = logging.getLogger(__name__)

# Valid PromQL durations: 5m, 1h, 30s, etc.
_WINDOW_RE = re.compile(r"^[1-9]\d*[smhdw]$")
_PRESSURE_CONDITIONS = ("MemoryPressure", "DiskPressure", "PIDPressure")


# ---------------------------------------------------------------------------
# Metric name configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class K8sMetricNames:
    """Metric and label names; override to match your exporter setup."""

    cpu_usage: str = "container_cpu_usage_seconds_total"
    memory_usage: str = "container_memory_working_set_bytes"
    restarts_total: str = "kube_pod_container_status_restarts_total"
    last_terminated_reason: str = "kube_pod_container_status_last_terminated_reason"
    pod_info: str = "kube_pod_info"
    node_condition: str = "kube_node_status_condition"

    pod_label: str = "pod"
    namespace_label: str = "namespace"
    container_label: str = "container"


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

@dataclass
class K8sMetrics:
    """Point-in-time metrics for one pod."""

    namespace: str
    pod: str
    window: str

    cpu_cores: Optional[float] = None
    memory_bytes: Optional[float] = None

    restarts_total: Optional[int] = None
    restarts_in_window: Optional[int] = None

    oom_killed: Optional[bool] = None

    # condition -> True/False/None
    node_pressure: Dict[str, Optional[bool]] = field(default_factory=dict)

    # key -> error message
    errors: Dict[str, str] = field(default_factory=dict)

    @property
    def under_node_pressure(self) -> Optional[bool]:
        """True if any pressure condition is firing; None if unknown."""
        known = [v for v in self.node_pressure.values() if v is not None]
        return any(known) if known else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape(value: str) -> str:
    """Escape a value for safe use inside PromQL label matchers."""
    return (
        value.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
    )


def _validate_window(window: str) -> str:
    if not _WINDOW_RE.match(window):
        raise ValueError(f"invalid PromQL duration {window!r} (expected '5m', '1h', etc.)")
    return window


def _to_int(value: Optional[float]) -> Optional[int]:
    """increase() is extrapolated, so round instead of truncating."""
    return None if value is None else int(round(value))


# ---------------------------------------------------------------------------
# PromQL builder (pure function → easy to unit test)
# ---------------------------------------------------------------------------

def build_queries(
    namespace: str,
    pod_name: str,
    window: str,
    names: K8sMetricNames,
) -> Dict[str, str]:
    ns = f'{names.namespace_label}="{_escape(namespace)}"'
    pod = f'{names.pod_label}="{_escape(pod_name)}"'
    sel = f"{ns}, {pod}"

    # Exclude cgroup aggregate + pause container
    real = f'{names.container_label}!="", {names.container_label}!="POD"'

    queries: Dict[str, str] = {
        "cpu_cores": f"sum(rate({names.cpu_usage}{{{sel}, {real}}}[{window}]))",
        "memory_bytes": f"sum({names.memory_usage}{{{sel}, {real}}})",

        "restarts_total": f"sum({names.restarts_total}{{{sel}}})",
        "restarts_in_window": f"sum(increase({names.restarts_total}{{{sel}}}[{window}]))",

        # kube-state-metrics only emits OOMKilled once → absence means "no OOM"
        "oom_killed": (
            f'max({names.last_terminated_reason}{{{sel}, reason="OOMKilled"}}) '
            f"or vector(0)"
        ),
    }

    # Node pressure conditions
    for cond in _PRESSURE_CONDITIONS:
        queries[f"pressure:{cond}"] = (
            f'max({names.node_condition}{{condition="{cond}", status="true"}} '
            f"* on(node) max by (node) ({names.pod_info}{{{sel}}}))"
        )

    return queries


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def get_k8s_metrics(
    namespace: str,
    pod_name: str,
    client: Optional[PrometheusClient] = None,
    window: str = "5m",
    names: Optional[K8sMetricNames] = None,
) -> K8sMetrics:
    """
    Collect pod-level Kubernetes metrics.

    Queries run in parallel. A failing query never fails the call; it leaves
    the field as None and records the reason in `errors`.
    """

    window = _validate_window(window)
    names = names or K8sMetricNames()
    client = client or PrometheusClient()

    queries = build_queries(namespace, pod_name, window, names)
    result = K8sMetrics(namespace=namespace, pod=pod_name, window=window)

    def run(item: Tuple[str, str]):
        key, promql = item
        try:
            return key, client.query_scalar(promql), None
        except PrometheusError as exc:
            logger.warning("k8s metric %s failed for %s/%s: %s", key, namespace, pod_name, exc)
            return key, None, str(exc)
        except Exception as exc:
            logger.exception("unexpected error for metric %s on %s/%s", key, namespace, pod_name)
            return key, None, f"unexpected: {exc!r}"

    # Run all PromQL queries concurrently
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        outcomes = list(pool.map(run, queries.items()))

    # Parse results
    raw: Dict[str, Optional[float]] = {}
    for key, value, err in outcomes:
        raw[key] = value
        if err:
            result.errors[key] = err

    # Assign fields
    result.cpu_cores = raw.get("cpu_cores")
    result.memory_bytes = raw.get("memory_bytes")

    result.restarts_total = _to_int(raw.get("restarts_total"))
    result.restarts_in_window = _to_int(raw.get("restarts_in_window"))

    oom = raw.get("oom_killed")
    result.oom_killed = None if oom is None else oom > 0

    for cond in _PRESSURE_CONDITIONS:
        v = raw.get(f"pressure:{cond}")
        result.node_pressure[cond] = None if v is None else v > 0

    return result
