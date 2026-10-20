import logging
from contextlib import contextmanager
from typing import Dict, List, Optional

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from sentence_transformers import SentenceTransformer

from backend.app.config import settings

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/all-mpnet-base-v2"  # 768 dims
MAX_TOP_K = 20

_model: Optional[SentenceTransformer] = None
_pool: Optional[ThreadedConnectionPool] = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(minconn=1, maxconn=5, dsn=settings.PG_DSN)
    return _pool


@contextmanager
def _connection():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.rollback()  # read-only; make sure no txn is left open
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def close_search() -> None:
    """Call from FastAPI lifespan shutdown."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def search_runbooks(
    query: str,
    top_k: int = 5,
    min_score: Optional[float] = None,
) -> List[Dict]:
    """
    Semantic search over runbook_chunks.
    Returns [{title, chunk, source, score}] sorted by similarity (higher = better, max 1.0).
    """
    query = query.strip()
    if not query:
        return []
    top_k = max(1, min(top_k, MAX_TOP_K))

    embedding = _get_model().encode(query, normalize_embeddings=True)
    vec = "[" + ",".join(f"{x:.6f}" for x in embedding) + "]"

    sql = """
        SELECT title, chunk, source, 1 - (embedding <=> %s::vector) AS score
        FROM runbook_chunks
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """

    with _connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, (vec, vec, top_k))
        rows = cur.fetchall()

    results = [{**r, "score": float(r["score"])} for r in rows]
    if min_score is not None:
        results = [r for r in results if r["score"] >= min_score]

    logger.info("runbook search q=%r hits=%d top=%.3f", query[:80], len(results),
                results[0]["score"] if results else 0.0)
    return results