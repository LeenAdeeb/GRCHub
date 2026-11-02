"""Schema versioning. schema.sql is the baseline (0001); later changes are numbered files in migrations/.
Safe to run any number of times and on an existing database: only files not yet recorded are applied.
The API runs this at startup, so upgrading the software upgrades the schema and keeps your data."""
from __future__ import annotations

from pathlib import Path

BASELINE = "0001_baseline"
SCHEMA_FILE = Path(__file__).with_name("schema.sql")
MIGRATIONS_DIR = Path(__file__).with_name("migrations")
LOCK_KEY = 724002


def migrate(conn) -> list[str]:
    """Bring the database to the current schema. Returns the names that were applied."""
    applied_now: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        if BASELINE not in done:
            exists = conn.execute("SELECT to_regclass('public.frameworks')").fetchone()[0] is not None
            if not exists:                       # empty database: create the baseline schema
                conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
                applied_now.append(BASELINE)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (BASELINE,))
            done.add(BASELINE)
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem not in done:
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.stem,))
                applied_now.append(path.stem)
    return applied_now
