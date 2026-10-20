import os
import re
import logging
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pgvector.psycopg import register_vector

from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

PG_DSN = os.environ.get("PG_DSN", "postgresql://postgres:postgres@localhost:5432/incidents")

# Anchored to this file, so it works from any cwd.
RUNBOOKS_DIR = Path(__file__).parent / "runbooks"


EMBEDDING_DIM = 768
MIN_CHUNK_CHARS = 20
MAX_CHUNK_CHARS = 1500


model = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")

def embed_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings using a free local model."""
    return model.encode(texts).tolist()


# ----------------------------- CHUNKING LOGIC -----------------------------------
def split_long(chunk: str) -> list[str]:
    if len(chunk) <= MAX_CHUNK_CHARS:
        return [chunk]
    out, buf = [], ""
    for para in chunk.split("\n\n"):
        if buf and len(buf) + len(para) > MAX_CHUNK_CHARS:
            out.append(buf.strip())
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        out.append(buf.strip())
    return out


def chunk_text(text: str) -> list[str]:
    chunks = []
    current = []
    path = []
    in_fence = False

    def flush():
        body = "\n".join(current).strip()
        if len(body.replace("#", "").strip()) >= MIN_CHUNK_CHARS:
            ctx = " > ".join(path)
            chunks.extend(split_long(f"[{ctx}]\n{body}" if ctx else body))
        current.clear()

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush()
            level = len(m.group(1))
            path[:] = path[: level - 1] + [m.group(2).strip()]
        current.append(line)
    flush()
    return chunks


def infer_service_from_filename(path: Path) -> str:
    return re.split(r"[_-]", path.stem)[0]


# ----------------------------- INGESTION PIPELINE -------------------------------
def load_runbook_files() -> list[Path]:
    return sorted(RUNBOOKS_DIR.glob("*.md"))


def ingest():
    files = load_runbook_files()
    if not files:
        log.warning("No runbook files found in %s", RUNBOOKS_DIR)
        return

    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
        register_vector(conn)

        # Remove chunks for runbooks that were deleted
        current_names = [p.name for p in files]
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM runbook_chunks WHERE NOT (metadata->>'file' = ANY(%s))",
                    (current_names,),
                )
                if cur.rowcount:
                    log.info("Removed %d stale chunks", cur.rowcount)

        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
                chunks = chunk_text(text)
                if not chunks:
                    log.warning("No usable chunks in %s, skipping", path.name)
                    continue

                embeddings = embed_batch(chunks)
                if len(embeddings) != len(chunks):
                    raise ValueError("embedding count != chunk count")

                for e in embeddings:
                    if len(e) != EMBEDDING_DIM:
                        raise ValueError(f"embedding dim {len(e)} != {EMBEDDING_DIM}")

                service = infer_service_from_filename(path)

                rows = [
                    (
                        chunk,
                        Jsonb({"service": service, "file": path.name, "chunk_index": idx}),
                        emb,   # <-- list of floats, automatically adapted
                    )
                    for idx, (chunk, emb) in enumerate(zip(chunks, embeddings))
                ]

                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM runbook_chunks WHERE metadata->>'file' = %s",
                            (path.name,),
                        )
                        cur.executemany(
                            "INSERT INTO runbook_chunks (text, metadata, embedding) VALUES (%s, %s, %s)",
                            rows,
                        )

                log.info("Ingested %d chunks from %s", len(chunks), path.name)

            except Exception:
                log.exception("Failed to ingest %s", path.name)
                continue


if __name__ == "__main__":
    ingest()
    print("Runbook ingestion complete.")
