from typing import Dict, Any, List

def correlate_signals(state: Dict[str, Any]) -> Dict[str, Any]:
    service = state.get("service")
    metrics = state.get("metrics", {})
    logs: List[str] = state.get("logs", [])
    db_results: List[Dict[str, Any]] = state.get("database_results", [])

    signals: List[str] = []

    # Metrics-based signals
    latency = metrics.get("latency_p95_ms")
    error_rate = metrics.get("error_rate")
    cpu = metrics.get("cpu_usage")

    if latency is not None and latency > 500:
        signals.append(f"[SIGNAL] High latency for {service}: p95={latency} ms")

    if error_rate is not None and error_rate > 0.05:
        signals.append(f"[SIGNAL] Elevated error rate for {service}: {error_rate * 100:.2f}%")

    if cpu is not None and cpu > 80:
        signals.append(f"[SIGNAL] High CPU usage on {service}: {cpu:.2f}%")

    # Logs-based signals
    for log in logs:
        if "timeout" in log.lower():
            signals.append(f"[SIGNAL] Timeout detected in logs: {log}")
        if "connection pool exhausted" in log.lower():
            signals.append(f"[SIGNAL] Connection pool exhaustion in logs: {log}")
        if "retry" in log.lower():
            signals.append(f"[SIGNAL] Retry storm indicated in logs: {log}")

    # DB-based signals
    for row in db_results:
        event = row.get("event", "").lower()
        if "timeout" in event:
            signals.append(f"[SIGNAL] DB timeout event: {row}")
        if "pool exhaustion" in event:
            signals.append(f"[SIGNAL] DB pool exhaustion event: {row}")
        if "slow query" in event:
            signals.append(f"[SIGNAL] Slow query event: {row}")

    # SAFE: mutate state instead of replacing it
    state["correlation_signals"] = signals
    return state


def correlation_node(state: Dict[str, Any]) -> Dict[str, Any]:
    # Run correlation
    correlate_signals(state)

    # SAFE: mutate existing observations list
    observations = state.get("observations", [])
    observations.append(f"[CORRELATION] Signals: {state.get('correlation_signals', [])}")

    state["observations"] = observations
    return state
