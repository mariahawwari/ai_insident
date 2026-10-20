"""
Database-level health metrics via Prometheus (postgres_exporter).

Collects:
- connection pool usage (0..1)
- connections for this database
- deadlocks over a window
- slow queries over a window (optional)
- replication lag (seconds)

Contract:
- Missing / NaN / Inf -> None
- A failed query never raises; it is recorded in DbMetrics.errors
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Tuple

from .prom_query import PrometheusClient, PrometheusError

logger = logging.getLogger(__name__)

# Valid PromQL durations: 5m, 1h, 30s, etc.
_WINDOW_RE = re.compile(r"^\d+[smhdw]$")
_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


# ---------------------------------------------------------------------------
# Metric name configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DbMetricNames:
    """Prometheus metric / label names. Set metric to None to disable it."""

    connections: str = "pg_stat_activity_count"
    max_connections: str = "pg_settings_max_connections"
    deadlocks: str = "pg_stat_database_deadlocks"

    # Optional: requires custom postgres_exporter queries.yaml
    slow_queries: Optional[str] = None

    replication_lag_seconds: Optional[str] = "pg_replication_lag_seconds"

    db_label: str = "datname"
    instance_label: str = "instance"

    def __post_init__(self) -> None:
        for label in (self.db_label, self.instance_label):
            if not _LABEL_RE.match(label):
                raise ValueError(f"invalid label name: {label!r}")


_DEFAULT_NAMES = DbMetricNames()


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

@dataclass
class DbMetrics:
    """Database health snapshot. None means 'no data', not zero."""

    database: str
    window: str

    pool_usage: Optional[float] = None
    db_connections: Optional[int] = None
    deadlocks: Optional[int] = None
    slow_queries: Optional[int] = None
    replication_lag_seconds: Optional[float] = None

    errors: Dict[str, str] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
    )


def _clean(value: Optional[float], as_int: bool) -> Optional[float | int]:
    """Normalize scalar: None/NaN/Inf -> None; optionally round to int."""
    if value is None or not math.isfinite(value):
        return None
    return int(round(value)) if as_int else float(value)


# ---------------------------------------------------------------------------
# PromQL builder
# ---------------------------------------------------------------------------

def _build_queries(
    database: str,
    window: str,
    n: DbMetricNames,
) -> Dict[str, Tuple[str, bool]]:
    """
    Return {field: (promql, as_int)}.
    Disabled metrics (None) are omitted.
    """

    db_sel = f'{n.db_label}="{_escape(database)}"'
    inst = n.instance_label

    queries: Dict[str, Tuple[str, bool]] = {
        # Pool usage = connections / max_connections per instance → worst instance
        "pool_usage": (
            f"max("
            f"sum by ({inst}) ({n.connections})"
            f" / on ({inst}) max by ({inst}) ({n.max_connections})"
            f")",
            False,
        ),

        # Connections for this database
        "db_connections": (
            f"sum({n.connections}{{{db_sel}}})",
            True,
        ),

        # Deadlocks over window
        "deadlocks": (
            f"sum(increase({n.deadlocks}{{{db_sel}}}[{window}]))",
            True,
        ),
    }

    # Optional slow queries
    if n.slow_queries:
        queries["slow_queries"] = (
            f"sum(increase({n.slow_queries}{{{db_sel}}}[{window}]))",
            True,
        )

    # Optional replication lag
    if n.replication_lag_seconds:
        queries["replication_lag_seconds"] = (
            f"max({n.replication_lag_seconds})",
            False,
        )

    return queries


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def get_db_metrics(
    database: str,
    client: Optional[PrometheusClient] = None,
    names: DbMetricNames = _DEFAULT_NAMES,
    window: str = "15m",
) -> DbMetrics:
    """
    Collect health metrics for a single Postgres database.

    Missing data → None.
    Failed queries → recorded in errors.
    """

    if not database:
        raise ValueError("database must be non-empty")
    if not _WINDOW_RE.match(window):
        raise ValueError(f"invalid window {window!r}; expected like '15m'")

    client = client or PrometheusClient()
    metrics = DbMetrics(database=database, window=window)
    queries = _build_queries(database, window, names)

    def run(item: Tuple[str, Tuple[str, bool]]):
        key, (promql, as_int) = item
        try:
            raw = client.query_scalar(promql)
            return key, _clean(raw, as_int), None
        except PrometheusError as exc:
            logger.warning("db metric %s failed for %s: %s", key, database, exc)
            return key, None, str(exc)
        except Exception as exc:
            logger.exception("unexpected error for db metric %s on %s", key, database)
            return key, None, f"unexpected: {exc!r}"

    # Run all PromQL queries concurrently
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        for key, value, err in pool.map(run, queries.items()):
            setattr(metrics, key, value)
            if err:
                metrics.errors[key] = err

    return metrics
