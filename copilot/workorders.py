"""Work order drafts. Nothing leaves the copilot until a supervisor approves.

Per FS-STD-001 6.2 a supervisor approves every work order before it is scheduled; in the EU the team lead acts as
supervisor. Here that is anyone in role-lead. Approval is where a real deployment would push to Salesforce.
"""
import psycopg
from psycopg.types.json import Jsonb

from .access import User
from .answer import Citation, WorkOrderDraft


class NotAllowed(Exception):
    pass


class NotPending(Exception):
    pass


def save_draft(conn: psycopg.Connection, user: User, question: str, draft: WorkOrderDraft,
               citations: list[Citation]) -> dict:
    row = conn.execute(
        "INSERT INTO work_orders (requested_by, question, draft, sources) VALUES (%s, %s, %s, %s) RETURNING *",
        (user.id, question, Jsonb(draft.model_dump()),
         Jsonb([{"title": c.title, "location": c.location, "path": c.path} for c in citations])),
    ).fetchone()
    conn.commit()
    return row


def list_all(conn: psycopg.Connection) -> list[dict]:
    return conn.execute("SELECT * FROM work_orders ORDER BY id DESC").fetchall()


def decide(conn: psycopg.Connection, work_order_id: int, user: User, approve: bool) -> dict:
    if not user.is_lead:
        raise NotAllowed(f"{user.name} is not a supervisor; only team leads approve work orders")
    # Only a draft can be decided, and only once (the WHERE makes two clicks race-safe).
    row = conn.execute(
        """UPDATE work_orders SET status = %s, decided_by = %s, decided_at = now()
           WHERE id = %s AND status = 'draft' RETURNING *""",
        ("approved" if approve else "rejected", user.id, work_order_id),
    ).fetchone()
    conn.commit()
    if row is None:
        raise NotPending(f"Work order {work_order_id} does not exist or was already decided")
    return row
