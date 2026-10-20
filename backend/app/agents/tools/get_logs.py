import logging
import os
import re
from functools import lru_cache
from typing import List

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

# Environment configuration
NAMESPACE = os.getenv("K8S_NAMESPACE", "default")
SERVICE_LABEL = os.getenv("K8S_SERVICE_LABEL", "app")
TAIL_LINES = int(os.getenv("LOG_TAIL_LINES", "200"))
SINCE_SECONDS = int(os.getenv("LOG_SINCE_SECONDS", "900"))
MAX_LINES = int(os.getenv("LOG_MAX_LINES", "500"))

_VALID_SERVICE = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$")


@lru_cache(maxsize=1)
def _api() -> client.CoreV1Api:
    """Load Kubernetes API client once."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.CoreV1Api()


def _read(api: client.CoreV1Api, pod: str, container: str, previous: bool) -> List[str]:
    """
    Read logs from a single container (current or previous instance).
    Returns clean UTF‑8 decoded lines tagged with timestamps and container name.
    """
    try:
        resp = api.read_namespaced_pod_log(
            name=pod,
            namespace=NAMESPACE,
            container=container,
            tail_lines=TAIL_LINES,
            since_seconds=None if previous else SINCE_SECONDS,
            timestamps=True,
            previous=previous,
            _preload_content=False,   # critical: avoid b'...' and escaped newlines
        )

        raw = resp.data.decode("utf-8", errors="replace")

    except ApiException as e:
        if e.status == 400:
            return []  # no previous container instance
        return [f"[ERROR] get_logs: cannot read {pod}/{container}: {e.status} {e.reason}"]

    tag = f"{pod}/{container}" + (" (previous)" if previous else "")
    out: List[str] = []

    for line in raw.splitlines():
        if not line.strip():
            continue
        ts, _, msg = line.partition(" ")
        out.append(f"{ts} [{tag}] {msg}")

    return out


def _status_lines(pod: client.V1Pod) -> List[str]:
    """
    Extract container state evidence (OOMKilled, CrashLoopBackOff, exit codes).
    These do NOT appear in logs but are critical for incident analysis.
    """
    lines: List[str] = []

    for cs in pod.status.container_statuses or []:
        name = f"{pod.metadata.name}/{cs.name}"

        if cs.state and cs.state.waiting and cs.state.waiting.reason:
            lines.append(
                f"[WARN] {name} waiting: {cs.state.waiting.reason} "
                f"{cs.state.waiting.message or ''}".strip()
            )

        last = cs.last_state.terminated if cs.last_state else None
        if last:
            lines.append(
                f"[WARN] {name} last terminated: reason={last.reason} "
                f"exit_code={last.exit_code} restarts={cs.restart_count}"
            )

    return lines


def get_logs(namespace: str, pod: str) -> List[str]:
    """
    Collect recent logs for a specific pod in Kubernetes.

    - Reads logs from all containers
    - Includes previous container logs if restarted
    - Adds container state evidence (OOMKilled, CrashLoopBackOff)
    - Merges logs chronologically
    - Caps output to MAX_LINES
    - Returns clean UTF‑8 decoded lines
    """
    try:
        api = _api()
        pod_obj = api.read_namespaced_pod(name=pod, namespace=namespace)
    except Exception as e:
        logger.exception("get_logs: kubernetes access failed")
        return [f"[ERROR] get_logs: kubernetes access failed: {e}"]

    status_lines = _status_lines(pod_obj)
    logs: List[str] = []

    restarts = {cs.name: cs.restart_count for cs in pod_obj.status.container_statuses or []}

    for container in pod_obj.spec.containers:
        name = container.name

        # Current logs
        logs.extend(_read(api, pod_obj.metadata.name, name, previous=False))

        # Previous logs (if restarted)
        if restarts.get(name, 0) > 0:
            logs.extend(_read(api, pod_obj.metadata.name, name, previous=True))

    logs.sort()          # RFC3339 timestamps → lexicographic == chronological
    logs = logs[-MAX_LINES:]

    return status_lines + logs


def get_pod_logs(namespace: str, pod: str) -> List[str]:
    """
    Fetch clean, readable logs from a specific Kubernetes pod.
    Simple, direct pod log reader for debugging and ad‑hoc inspection.
    """
    try:
        api = _api()

        resp = api.read_namespaced_pod_log(
            name=pod,
            namespace=namespace,
            tail_lines=TAIL_LINES,
            timestamps=True,
            _preload_content=False,
        )

        raw = resp.data.decode("utf-8", errors="replace")
        lines = [line for line in raw.splitlines() if line.strip()]

        return lines

    except ApiException as e:
        return [f"[ERROR] get_pod_logs: cannot read {pod}: {e.status} {e.reason}"]

    except Exception as e:
        logger.exception("get_pod_logs: unexpected failure")
        return [f"[ERROR] get_pod_logs: unexpected failure: {e}"]
