from __future__ import annotations

from psycopg.types.json import Jsonb


def audit(conn, actor: str | None, action: str, entity: str, entity_id, detail: dict | None = None,
          org_id: int | None = None) -> None:
    """Append one row to the audit log. Call inside the same transaction as the change it records."""
    conn.execute(
        "INSERT INTO audit_logs (org_id, actor, action, entity, entity_id, detail) VALUES (%s,%s,%s,%s,%s,%s)",
        (org_id, actor, action, entity, str(entity_id), Jsonb(detail or {})))
