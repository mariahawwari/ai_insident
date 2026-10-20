
from typing import Optional, List, Dict, Any
from backend.app.agents.types.diagnostics import Check
from .rules import RULES




def make_run_diagnostics_node(checks: Optional[List[Check]] = None):
    checks = checks or []

    def run_diagnostics_node(state: Dict[str, Any]) -> Dict[str, Any]:
        service = state.get("service")
        signals = [_signal_text(s).lower() for s in state.get("correlation_signals", [])]
        obs = list(state.get("observations") or [])

        if not service:
            diagnostics = {
                "status": "unknown",
                "checks": [],
                "findings": [],
                "next_steps": ["No service in state; cannot run diagnostics"],
            }
            msg = "[DIAGNOSTICS] Status: unknown. No service specified."

            # SAFE: mutate state instead of replacing it
            state["diagnostics"] = diagnostics
            obs.append(msg)
            state["observations"] = obs
            return state

        # Phase 1: gather evidence
        check_results = []
        for check in checks:
            try:
                check_results.append(check(service))
            except Exception as e:
                name = getattr(check, "__name__", "check")
                check_results.append(
                    {"name": name, "ok": False, "detail": f"check raised: {e}"}
                )

        # Phase 2: interpret rules
        findings: List[Dict[str, Any]] = []
        next_steps: List[str] = []

        for rule in RULES:
            evidence = [s for s in signals if rule.pattern.search(s)]
            if evidence:
                findings.append(
                    {
                        "rule": rule.name,
                        "summary": rule.finding,
                        "severity": rule.severity,
                        "evidence": evidence,
                    }
                )
                for step in rule.next_steps:
                    if step not in next_steps:
                        next_steps.append(step)

        # Add failed checks
        failed = [c for c in check_results if not c.get("ok")]
        for c in failed:
            findings.append(
                {
                    "rule": "check_failed",
                    "summary": f"Check failed: {c.get('name')}",
                    "severity": "critical",
                    "evidence": [c.get("detail", "")],
                }
            )

        # Phase 3: conclude
        if findings:
            status = "degraded"
        elif check_results:
            status = "healthy"
        else:
            status = "unknown"
            next_steps.append(
                "No checks ran and no signals matched; gather more data before concluding"
            )

        diagnostics = {
            "status": status,
            "checks": check_results,
            "findings": findings,
            "next_steps": next_steps,
        }

        summary = ", ".join(f["summary"] for f in findings) or "none"
        msg = (
            f"[DIAGNOSTICS] {service}: {status}. "
            f"Findings: {summary}. Next steps: {'; '.join(next_steps) or 'none'}."
        )

        # SAFE: mutate state instead of replacing it
        state["diagnostics"] = diagnostics
        obs.append(msg)
        state["observations"] = obs

        return state

    return run_diagnostics_node
