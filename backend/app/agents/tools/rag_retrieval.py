"""RAG retrieval tool for the SRE incident agent.

Contract:
- Raises on real failures (DB down, embedding error, missing DATABASE_URL).
  The caller (rag_node) is responsible for catching and reporting.
- Returns [] when the query ran fine but nothing was relevant enough.

Requires: numpy, psycopg[binary], pgvector, sentence-transformers
Assumes table: runbook_chunks(text TEXT, metadata JSONB, embedding vector(384))
"""

import logging
import os
import re
import threading
from typing import Optional

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- config
EMBEDDING_MODEL_NAME = os.getenv("RAG_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
TOP_K = int(os.getenv("RAG_TOP_K", "5"))
# Cosine distance: 0 = identical, 2 = opposite. TUNE this on real examples.
MAX_DISTANCE = float(os.getenv("RAG_MAX_DISTANCE", "0.6"))
MAX_QUERY_LINES = 5
MAX_QUERY_CHARS = 500

# ---------------------------------------------------------------- embedding
_model: Optional[SentenceTransformer] = None
_model_lock = threading.Lock()


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def embed(text: str) -> np.ndarray:
    """Normalized float32 vector. Must match the model used at ingestion."""
    vector = _get_model().encode(text, normalize_embeddings=True)
    return np.asarray(vector, dtype=np.float32)


# ---------------------------------------------------------------- query building
_NOISE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\b[0-9a-f]{8,}\b"
    r"|\b\d+\b",
    re.IGNORECASE,
)

_SEVERITY = re.compile(
    r"\b(error|err|fatal|critical|exception|timeout|timed out|refused|"
    r"exhausted|oom|crashloop|failed|failure|warn|warning)\b",
    re.IGNORECASE,
)


def _clean_line(line: str) -> str:
    line = _NOISE.sub(" ", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip(" -:|[]")


def _build_query_text(service: str, logs: list[str]) -> str:
    """Turn raw logs into a short, sentence-like query that resembles runbook prose."""
    seen = set()
    severe, other = [], []
    for raw in logs:
        line = _clean_line(raw)
        if len(line) < 5 or line in seen:
            continue
        seen.add(line)
        (severe if _SEVERITY.search(line) else other).append(line)

    selected = (severe + other)[:MAX_QUERY_LINES]
    text = f"{service}: " + ". ".join(selected)
    return text[:MAX_QUERY_CHARS]


# ---------------------------------------------------------------- database
def _get_connection() -> psycopg.Connection:
    dsn = os.getenv("AGENT_DB_DSN")
    if not dsn:
        raise RuntimeError("AGENT_DB_DSN environment variable is not set")
    conn = psycopg.connect(dsn)
    register_vector(conn)

    with conn.cursor() as cur:
        cur.execute("select current_database(), current_schema(), inet_server_port()")
        db_info = cur.fetchone()
        print("DB INFO:", db_info, flush=True)

    return conn


_SEARCH_SQL = """
    SELECT text, metadata, embedding <=> %(q)s AS distance
    FROM runbook_chunks
    WHERE metadata->>'service' = %(service)s
    ORDER BY embedding <=> %(q)s
    LIMIT %(k)s;
"""


# ---------------------------------------------------------------- tool
def rag_retrieval(service: str, logs: list[str]) -> list[dict]:
    """Return runbook chunks relevant to the service's logs.

    Each result: {"text": str, "metadata": {..., "service": str, "distance": float}}
    Lower distance = more relevant. Results above MAX_DISTANCE are dropped.
    """
    print("RAG TOOL CALLED", flush=True)

    # 1) Validate logs
    if not logs:
        logger.warning("rag_retrieval called with no logs for service=%s", service)
        return []

    # 2) Build query text
    print("ABOUT TO BUILD QUERY", flush=True)
    query_text = _build_query_text(service, logs)
    print("QUERY TEXT:", query_text, flush=True)

    # 3) Embed query
    print("ABOUT TO EMBED", flush=True)
    query_vec = embed(query_text)
    print("EMBEDDING DONE", flush=True)

    # 4) Connect and query
    print("ABOUT TO CONNECT", flush=True)
    with _get_connection() as conn:
        print("CONNECTED; ABOUT TO QUERY", flush=True)
        with conn.cursor() as cur:
            print("EXECUTING SQL...", flush=True)
            cur.execute(_SEARCH_SQL, {"q": query_vec, "service": service, "k": TOP_K})
            rows = cur.fetchall()
            print(f"SQL DONE — fetched {len(rows)} rows", flush=True)

    # 5) Filter results
    results = []
    for text, metadata, distance in rows:
        if distance > MAX_DISTANCE:
            continue
        merged = dict(metadata) if metadata else {}
        merged.setdefault("service", service)
        merged["distance"] = float(distance)
        results.append({"text": text, "metadata": merged})

    logger.info(
        "rag_retrieval service=%s query=%r fetched=%d kept=%d best_distance=%s",
        service,
        query_text,
        len(rows),
        len(results),
        f"{float(rows[0][2]):.3f}" if rows else "n/a",
    )
    print(f"FINAL RESULTS: kept {len(results)} rows", flush=True)
    return results


# ---------------------------------------------------------------- smoke test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sample_logs = [
        "2026-10-06T12:03:11Z ERROR payment-api QueuePool limit of 10 overflow 5 reached, connection timed out",
        "2026-10-06T12:03:12Z WARN payment-api request 7f9c8d6b5a retrying db connection",
        "2026-10-06T12:03:12Z INFO payment-api health check ok",
    ]
    print("QUERY:", _build_query_text("payment-api", sample_logs))
    for hit in rag_retrieval("payment-api", sample_logs):
        print(round(hit["metadata"]["distance"], 3), hit["text"][:100])
