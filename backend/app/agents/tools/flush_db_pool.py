import os
import time
from contextlib import closing
from typing import Dict, Any

import psycopg2

# service -> application_name its connections use (set via the app's connection string)
POOL_FLUSHABLE = {"checkout": "checkout"}
COOLDOWN_S = 600
MIN_IDLE_S = 30      # only touch sessions idle at least this long
MAX_KILL = 50        # hard cap per call
_last_flush: Dict[str, float] = {}  # in-memory, resets on process restart

_IDLE_STATES = ("idle", "idle in transaction", "idle in transaction (aborted)")

_PREVIEW_SQL = """
SELECT state, count(*)
FROM pg_stat_activity
WHERE application_name = %s
  AND backend_type = 'client backend'
  AND datname = current_database()
  AND pid <> pg_backend_pid()
GROUP BY state
"""

_KILL_SQL = """
SELECT pid, pg_terminate_backend(pid) AS ok
FROM (
    SELECT pid
    FROM pg_stat_activity
    WHERE application_name = %s
      AND backend_type = 'client backend'
      AND datname = current_database()
      AND pid <> pg_backend_pid()
      AND state = ANY(%s)
      AND state_change < now() - make_interval(secs => %s)
    ORDER BY state_change
    LIMIT %s
) t
"""


def _result(service: str, status: str, details: str, **extra) -> Dict[str, Any]:
    return {"action": "terminate_idle_db_sessions", "service": service,
            "status": status, "details": details, **extra}


def _connect():
    # DSN never passes through graph state
    return psycopg2.connect(
        os.environ["AGENT_DB_DSN"],
        connect_timeout=5,
        options="-c statement_timeout=5000 -c lock_timeout=2000",
    )


def terminate_idle_db_sessions(service: str, dry_run: bool = True) -> Dict[str, Any]:
    app_name = POOL_FLUSHABLE.get(service)
    if not app_name:
        return _result(service, "rejected", "Service is not in the DB-flush allowlist.")

    elapsed = time.time() - _last_flush.get(service, 0)
    if elapsed < COOLDOWN_S:
        return _result(service, "rejected", f"Cooldown active, last flush {elapsed:.0f}s ago.")

    try:
        with closing(_connect()) as conn, conn.cursor() as cur:
            if dry_run:
                cur.execute(_PREVIEW_SQL, (app_name,))
                by_state = {state or "unknown": n for state, n in cur.fetchall()}
                return _result(service, "dry_run",
                               f"Would terminate sessions idle >{MIN_IDLE_S}s (max {MAX_KILL}).",
                               sessions_by_state=by_state)

            cur.execute(_KILL_SQL, (app_name, list(_IDLE_STATES), MIN_IDLE_S, MAX_KILL))
            rows = cur.fetchall()
            attempted = len(rows)
            terminated = sum(1 for _, ok in rows if ok)

        _last_flush[service] = time.time()
        return _result(service, "triggered",
                       f"Terminated {terminated}/{attempted} idle sessions. Not yet verified.",
                       attempted=attempted, terminated=terminated)
    except Exception as e:
        return _result(service, "error", f"{type(e).__name__}: {e}")