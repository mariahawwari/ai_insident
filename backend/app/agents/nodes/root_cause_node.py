"""
root_cause_node.py

Reasoning stage of the SRE incident agent.

Pipeline position:  metrics -> logs -> database -> rag_node -> correlation -> root_cause -> report

Design rules:
- This node does NOT call external LLM APIs.
- It uses a local SentenceTransformer model to compare evidence against simple patterns.
- It reads whatever evidence exists (tolerates missing keys), so you can add
  logs / db / correlation / rag nodes later without touching this file.
- Output keeps the keys your report node already expects:
  hypothesis, confidence, evidence, next_checks (+ extras).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Dict, List

from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MAX_EVIDENCE = 25
MAX_DOCS = 4
MAX_ITEM_CHARS = 600
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "unknown": 0}

SIGNAL_KEYS = {
    "metrics": "metrics",
    "logs": "logs",
    "database": "database_results",
}

CORRELATION_KEY = "correlation_signals"
DOCS_KEYS = ("retrieved_docs", "rag_results")


# ---------------------------------------------------------------------------
# Local model + patterns
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    # CPU-friendly model
    return SentenceTransformer("sentence-transformers/all-mpnet-base-v2")


FAILURE_PATTERNS: Dict[str, List[str]] = {
    "database": [
        "timeout",
        "connection refused",
        "slow queries",
        "pool exhaustion",
        "deadlock",
        "transaction timeout",
    ],
    "cache": [
        "eviction storm",
        "cache miss spike",
        "redis latency",
        "stale cache",
        "cache unavailable",
    ],
    "network": [
        "packet loss",
        "latency spike",
        "dns failure",
        "connection reset",
        "load balancer issue",
    ],
    "kubernetes": [
        "CrashLoopBackOff",
        "OOMKilled",
        "pod restart",
        "node pressure",
        "insufficient resources",
    ],
}


# ---------------------------------------------------------------------------
# Output schema (for clarity / consistency)
# ---------------------------------------------------------------------------
class RootCauseLocal(BaseModel):
    hypothesis: str = Field(
        description="Most likely root cause in 1–2 sentences, naming the cause not just the symptom."
    )
    confidence: str = Field(description="One of: low, medium, high.")
    evidence: List[str] = Field(description="Evidence lines supporting the hypothesis.")
    evidence_sources: List[str] = Field(description="Sources of the evidence (metrics/logs/db/observation).")
    missing_or_contradicting: List[str] = Field(
        description="What is missing or contradicts the hypothesis."
    )
    runbook_refs: List[str] = Field(
        default_factory=list,
        description="Ids like D1 of runbook excerpts relevant to this cause.",
    )
    next_checks: List[str] = Field(
        description="Read-only checks that would confirm or refute the hypothesis."
    )
    recommended_actions: List[str] = Field(
        default_factory=list,
        description="Suggested remediation steps (not auto-executed)."
    )
    method: str = Field(description="Reasoning method used (local_embeddings / heuristic / none).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clip(text: str, n: int = MAX_ITEM_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _to_text(item: Any) -> str:
    if isinstance(item, str):
        return _clip(item)
    if hasattr(item, "page_content"):  # langchain Document
        return _clip(item.page_content)
    if isinstance(item, dict):
        for key in ("content", "text", "page_content", "document", "signal", "message"):
            if key in item and isinstance(item[key], str):
                return _clip(item[key])
        return _clip(json.dumps(item, default=str))
    return _clip(item)


def _flatten(source: str, data: Any) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if data in (None, "", [], {}):
        return rows

    if isinstance(data, dict):
        for k, v in data.items():
            rows.append({"source": source, "severity": "unknown", "text": _clip(f"{k}: {v}")})

    elif isinstance(data, (list, tuple)):
        for item in data:
            sev = item.get("severity", "unknown") if isinstance(item, dict) else "unknown"
            rows.append({"source": source, "severity": str(sev).lower(), "text": _to_text(item)})

    else:
        rows.append({"source": source, "severity": "unknown", "text": _clip(data)})

    return rows


def _collect_evidence(state: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []

    # 1) Correlation signals
    for s in state.get(CORRELATION_KEY) or []:
        if isinstance(s, dict):
            rows.append(
                {
                    "source": str(s.get("source", "correlation")),
                    "severity": str(s.get("severity", "unknown")).lower(),
                    "text": _clip(s.get("signal", s)),
                }
            )

    # 2) Raw outputs from metrics/logs/db nodes
    for source, key in SIGNAL_KEYS.items():
        rows.extend(_flatten(source, state.get(key)))

    # 3) Observations (skip root_cause/report lines)
    for o in state.get("observations") or []:
        o = str(o)
        if o.startswith(("[ROOT_CAUSE]", "[REPORT]")):
            continue
        rows.append({"source": "observation", "severity": "unknown", "text": _clip(o)})

    # Deduplicate + rank + cap + assign ids
    seen, unique = set(), []
    for r in rows:
        key = (r["source"], r["text"])
        if key not in seen:
            seen.add(key)
            unique.append(r)

    unique.sort(key=lambda r: SEVERITY_RANK.get(r["severity"], 0), reverse=True)
    unique = unique[:MAX_EVIDENCE]

    for i, r in enumerate(unique, 1):
        r["id"] = f"E{i}"

    return unique


def _collect_docs(state: Dict[str, Any]) -> List[Dict[str, str]]:
    raw: Any = []
    for key in DOCS_KEYS:
        if state.get(key):
            raw = state[key]
            break

    if isinstance(raw, (str, dict)) or hasattr(raw, "page_content"):
        raw = [raw]

    docs = []
    for i, item in enumerate(list(raw)[:MAX_DOCS], 1):
        name = None
        if isinstance(item, dict):
            name = item.get("source") or item.get("title")
        elif hasattr(item, "metadata"):
            name = (item.metadata or {}).get("source")

        text = _to_text(item)
        docs.append({"id": f"D{i}", "text": f"(runbook: {name}) {text}" if name else text})

    return docs


# ---------------------------------------------------------------------------
# Local embedding-based analysis
# ---------------------------------------------------------------------------
def _local_analyze(service: str, evidence: List[dict], docs: List[dict]) -> Dict[str, Any]:
    """
    Use local embeddings to estimate which failure pattern best matches the evidence.
    """
    if not evidence:
        return _insufficient()

    model = _get_model()

    # Embed evidence texts
    ev_texts = [e["text"] for e in evidence]
    ev_emb = model.encode(ev_texts)

    best_category = None
    best_score = -1.0

    # Compare to each failure pattern category
    for category, keywords in FAILURE_PATTERNS.items():
        pat_emb = model.encode(keywords)
        # average dot product similarity
        sims = []
        for i in range(len(ev_emb)):
            for j in range(len(pat_emb)):
                sims.append(float(np.dot(ev_emb[i], pat_emb[j])))
        score = float(np.mean(sims)) if sims else -1.0

        if score > best_score:
            best_score = score
            best_category = category

    # Confidence heuristic
    if best_score < 0.25:
        confidence = "low"
    elif best_score < 0.45:
        confidence = "medium"
    else:
        confidence = "high"

    hypothesis = (
        f"Signals most closely match a {best_category} issue affecting service '{service}'."
        if best_category
        else f"Signals do not clearly match any known pattern for service '{service}'."
    )

    cited_evidence = [f"[{e['id']}] {e['text']}" for e in evidence[:5]]
    sources = sorted({e["source"] for e in evidence[:5]})

    missing = []
    if confidence == "low":
        missing.append("Evidence is weak or ambiguous; more diagnostics are needed.")
    if "metrics" not in sources:
        missing.append("No strong metrics evidence present.")
    if "logs" not in sources:
        missing.append("No strong log evidence present.")
    if "database" not in sources and best_category == "database":
        missing.append("Database-specific signals are limited.")

    next_checks = [f"Verify {best_category} subsystem health"] if best_category else [
        "Review metrics for anomalies",
        "Review logs for recurring errors",
        "Check database health",
    ]

    recommended_actions = []
    if best_category == "database":
        recommended_actions.append("Inspect DB connection pool usage and slow queries.")
    elif best_category == "cache":
        recommended_actions.append("Check cache hit rate and eviction behavior.")
    elif best_category == "network":
        recommended_actions.append("Check network latency, DNS, and load balancer health.")
    elif best_category == "kubernetes":
        recommended_actions.append("Inspect pod restart counts, OOMKilled events, and node pressure.")

    return RootCauseLocal(
        hypothesis=hypothesis,
        confidence=confidence,
        evidence=cited_evidence,
        evidence_sources=sources,
        missing_or_contradicting=missing,
        runbook_refs=[d["id"] for d in docs] if docs else [],
        next_checks=next_checks,
        recommended_actions=recommended_actions,
        method="local_embeddings",
    ).model_dump()


def _insufficient() -> Dict[str, Any]:
    return RootCauseLocal(
        hypothesis="Insufficient evidence to determine a root cause.",
        confidence="low",
        evidence=[],
        evidence_sources=[],
        missing_or_contradicting=["No metrics, logs, database or correlation data in state."],
        runbook_refs=[],
        next_checks=["Re-run diagnostics", "Check metrics", "Check logs", "Check database"],
        recommended_actions=[],
        method="none",
    ).model_dump()


# ---------------------------------------------------------------------------
# LangGraph node (API‑safe)
# ---------------------------------------------------------------------------
def root_cause_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Local, SentenceTransformer-based root cause node (no external LLM)."""
    service = state.get("service", "unknown-service")
    obs = list(state.get("observations") or [])

    evidence = _collect_evidence(state)
    docs = _collect_docs(state)

    if not evidence:
        rc = _insufficient()
    else:
        try:
            rc = _local_analyze(service, evidence, docs)
        except Exception:
            logger.exception("root_cause local analysis failed; falling back to insufficient")
            rc = _insufficient()

    obs.append(f"[ROOT_CAUSE] ({rc['confidence']}) {rc['hypothesis']}")

    # SAFE: mutate state instead of replacing it
    state["root_cause"] = rc
    state["observations"] = obs

    return state
