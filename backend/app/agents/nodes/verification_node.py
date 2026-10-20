# verification_node.py
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

from states import IncidentState
from tools.logs_tool import search_logs
from tools.metrics_tool import check_metrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config: thresholds live in one place, overridable via env, not buried in logic
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VerificationConfig:
    max_latency_ms: float = float(os.getenv("VERIFY_MAX_LATENCY_MS", 300))
    max_error_rate: float = float(os.getenv("VERIFY_MAX_ERROR_RATE", 0.05))
    max_new_restarts: int = int(os.getenv("VERIFY_MAX_NEW_RESTARTS", 0))
    max_attempts: int = int(os.getenv("VERIFY_MAX_ATTEMPTS", 3))
    # Poll instead of one-shot: a service needs time to settle after an action
    poll_interval_s: float = float(os.getenv("VERIFY_POLL_INTERVAL_S", 10))
    poll_attempts: int = int(os.getenv("VERIFY_POLL_ATTEMPTS", 6))
    # Need N consecutive healthy reads so one lucky sample doesn't pass us
    required_healthy_reads: int = int(os.getenv("VERIFY_HEALTHY_READS", 2))


CONFIG = VerificationConfig()


# ---------------------------------------------------------------------------
# Pure evaluation logic: no I/O, trivially unit-testable
# ---------------------------------------------------------------------------
def evaluate_metrics(
    metrics: dict[str, Any],
    baseline_restarts: int,
    cfg: VerificationConfig = CONFIG,
) -> list[str]:
    """Return a list of problems. Empty list == healthy.

    Missing metrics count as a problem (unknown != healthy).
    """
    problems: list[str] = []

    for key in ("latency_ms", "error_rate", "pod_restarts"):
        if metrics.get(key) is None:
            problems.append(f"missing metric: {key}")
    if problems:
        return problems

    if metrics["latency_ms"] > cfg.max_latency_ms:
        problems.append(f"latency {metrics['latency_ms']}ms > {cfg.max_latency_ms}ms")

    if metrics["error_rate"] > cfg.max_error_rate:
        problems.append(f"error_rate {metrics['error_rate']:.3f} > {cfg.max_error_rate}")

    # Compare against the pre-action baseline. Our own restart_service call
    # bumps the counter, so an absolute `> 0` check would always fail after it.
    new_restarts = metrics["pod_restarts"] - baseline_restarts
    if new_restarts > cfg.max_new_restarts:
        problems.append(f"{new_restarts} new pod restarts since action (crash loop?)")

    return problems


def _poll_until_stable(service: str, baseline_restarts: int, cfg: VerificationConfig):
    """Poll metrics until healthy for N consecutive reads or we run out of tries."""
    healthy_streak = 0
    metrics: dict[str, Any] = {}
    problems: list[str] = ["no metrics collected"]

    for i in range(cfg.poll_attempts):
        try:
            metrics = check_metrics(service)
            problems = evaluate_metrics(metrics, baseline_restarts, cfg)
        except Exception as exc:  # tool failure must never look like "healthy"
            logger.exception("check_metrics failed for %s", service)
            metrics, problems = {}, [f"metrics tool error: {exc}"]

        healthy_streak = healthy_streak + 1 if not problems else 0
        if healthy_streak >= cfg.required_healthy_reads:
            return metrics, []

        if i < cfg.poll_attempts - 1:
            time.sleep(cfg.poll_interval_s)

    return metrics, problems


# ---------------------------------------------------------------------------
# The node
# ---------------------------------------------------------------------------
def verification_node(state: IncidentState) -> dict[str, Any]:
    """Verify the incident is actually resolved after an action.

    Returns a partial state update (LangGraph merges it) instead of mutating
    and returning the whole state. Routing is done by `route_after_verification`.
    """
    service = state.service_name
    attempt = getattr(state, "verification_attempts", 0) + 1
    baseline = getattr(state, "baseline_pod_restarts", 0)

    logger.info("verification start service=%s attempt=%d/%d",
                service, attempt, CONFIG.max_attempts)

    metrics, problems = _poll_until_stable(service, baseline, CONFIG)

    # Logs are evidence for the report / next planning round, not a pass/fail gate
    try:
        logs = search_logs(service)
    except Exception as exc:
        logger.warning("search_logs failed for %s: %s", service, exc)
        logs = {"error": str(exc)}

    passed = not problems
    if passed:
        note = "[VERIFICATION] System recovered"
    else:
        note = f"[VERIFICATION] Attempt {attempt}: still degraded: " + "; ".join(problems)
    logger.info(note)

    return {
        "verification_metrics": metrics,
        "verification_logs": logs,
        "verification_passed": passed,
        "verification_problems": problems,  # planner can use this on retry
        "verification_attempts": attempt,
        # Assumes `observations` has NO reducer. If you use Annotated[list, operator.add],
        # return just [note] instead.
        "observations": [*state.observations, note],
    }


# ---------------------------------------------------------------------------
# Conditional edge: routing belongs in the graph, not in a state string
# ---------------------------------------------------------------------------
def route_after_verification(
    state: IncidentState,
) -> Literal["report_node", "plan_actions_node", "escalate_node"]:
    if state.verification_passed:
        return "report_node"
    if state.verification_attempts >= CONFIG.max_attempts:
        return "escalate_node"  # stop looping, page a human
    return "plan_actions_node"


