"""Data-access helpers over the SQLite store. Thin, explicit functions used by
the assistant's tools, the briefing engine, and the HTTP API.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import get_connection, rows_to_list, row_to_dict, utcnow


# --------------------------------------------------------------------------- #
# Users / family
# --------------------------------------------------------------------------- #
def ensure_default_user() -> dict[str, Any]:
    """Guarantee at least one user exists (the Principal). Returns it."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()
    if row:
        return dict(row)
    now = utcnow()
    conn.execute(
        "INSERT INTO users (name, role, preferences, created_at) VALUES (?,?,?,?)",
        ("Principal", "principal", "{}", now),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone())


def list_users() -> list[dict[str, Any]]:
    conn = get_connection()
    return rows_to_list(conn.execute("SELECT * FROM users ORDER BY id").fetchall())


def get_user(user_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    return row_to_dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


def create_user(name: str, role: str = "family", preferences: dict | None = None) -> dict[str, Any]:
    conn = get_connection()
    now = utcnow()
    cur = conn.execute(
        "INSERT INTO users (name, role, preferences, created_at) VALUES (?,?,?,?)",
        (name.strip(), role, json.dumps(preferences or {}), now),
    )
    conn.commit()
    return get_user(cur.lastrowid)  # type: ignore[return-value]


def update_user_preferences(user_id: int, preferences: dict) -> dict[str, Any] | None:
    conn = get_connection()
    conn.execute("UPDATE users SET preferences=? WHERE id=?", (json.dumps(preferences), user_id))
    conn.commit()
    return get_user(user_id)


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def _insert(table: str, data: dict[str, Any]) -> int:
    conn = get_connection()
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(data.values()))
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def _update(table: str, row_id: int, data: dict[str, Any]) -> None:
    if not data:
        return
    conn = get_connection()
    assignments = ", ".join(f"{k}=?" for k in data)
    conn.execute(f"UPDATE {table} SET {assignments} WHERE id=?", (*data.values(), row_id))
    conn.commit()


def _get(table: str, row_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    return row_to_dict(conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone())


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
PROJECT_FIELDS = {
    "name", "kind", "objective", "outcome", "owner", "stakeholders", "status",
    "priority", "deadline", "milestones", "dependencies", "risks", "blockers",
    "next_action", "notes", "user_id",
}


def create_project(**kw) -> dict[str, Any]:
    now = utcnow()
    data = {k: v for k, v in kw.items() if k in PROJECT_FIELDS and v is not None}
    data.setdefault("name", "Untitled project")
    data.update(created_at=now, updated_at=now)
    return _get("projects", _insert("projects", data))  # type: ignore[return-value]


def update_project(project_id: int, **kw) -> dict[str, Any] | None:
    data = {k: v for k, v in kw.items() if k in PROJECT_FIELDS and v is not None}
    data["updated_at"] = utcnow()
    _update("projects", project_id, data)
    return _get("projects", project_id)


def list_projects(status: str | None = None, include_closed: bool = False) -> list[dict[str, Any]]:
    conn = get_connection()
    q = "SELECT * FROM projects"
    args: list[Any] = []
    clauses = []
    if status:
        clauses.append("status=?")
        args.append(status)
    elif not include_closed:
        clauses.append("status NOT IN ('complete','shelved')")
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, updated_at DESC"
    return rows_to_list(conn.execute(q, args).fetchall())


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
TASK_FIELDS = {"title", "detail", "status", "priority", "category", "due", "project_id", "user_id"}


def create_task(**kw) -> dict[str, Any]:
    now = utcnow()
    data = {k: v for k, v in kw.items() if k in TASK_FIELDS and v is not None}
    data.setdefault("title", "Untitled task")
    data.update(created_at=now, updated_at=now)
    return _get("tasks", _insert("tasks", data))  # type: ignore[return-value]


def update_task(task_id: int, **kw) -> dict[str, Any] | None:
    data = {k: v for k, v in kw.items() if k in TASK_FIELDS and v is not None}
    if data.get("status") == "done":
        data.setdefault("completed_at", utcnow())
    data["updated_at"] = utcnow()
    _update("tasks", task_id, data)
    return _get("tasks", task_id)


def list_tasks(status: str | None = None, include_done: bool = False) -> list[dict[str, Any]]:
    conn = get_connection()
    q = "SELECT * FROM tasks"
    args: list[Any] = []
    clauses = []
    if status:
        clauses.append("status=?")
        args.append(status)
    elif not include_done:
        clauses.append("status NOT IN ('done','dropped')")
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += (
        " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
        "WHEN 'medium' THEN 2 ELSE 3 END, due IS NULL, due ASC"
    )
    return rows_to_list(conn.execute(q, args).fetchall())


def tasks_due_before(iso_dt: str, include_overdue: bool = True) -> list[dict[str, Any]]:
    conn = get_connection()
    q = (
        "SELECT * FROM tasks WHERE status NOT IN ('done','dropped') "
        "AND due IS NOT NULL AND due <= ? ORDER BY due ASC"
    )
    return rows_to_list(conn.execute(q, (iso_dt,)).fetchall())


# --------------------------------------------------------------------------- #
# Commitments
# --------------------------------------------------------------------------- #
COMMITMENT_FIELDS = {"description", "owed_by", "owed_to", "due", "status", "project_id"}


def create_commitment(**kw) -> dict[str, Any]:
    now = utcnow()
    data = {k: v for k, v in kw.items() if k in COMMITMENT_FIELDS and v is not None}
    data.setdefault("description", "Unspecified commitment")
    data.update(created_at=now, updated_at=now)
    return _get("commitments", _insert("commitments", data))  # type: ignore[return-value]


def update_commitment(commitment_id: int, **kw) -> dict[str, Any] | None:
    data = {k: v for k, v in kw.items() if k in COMMITMENT_FIELDS and v is not None}
    data["updated_at"] = utcnow()
    _update("commitments", commitment_id, data)
    return _get("commitments", commitment_id)


def list_commitments(status: str | None = "open") -> list[dict[str, Any]]:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM commitments WHERE status=? ORDER BY due IS NULL, due ASC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM commitments ORDER BY due IS NULL, due ASC").fetchall()
    return rows_to_list(rows)


# --------------------------------------------------------------------------- #
# People
# --------------------------------------------------------------------------- #
PEOPLE_FIELDS = {"name", "relationship", "context", "last_contact", "follow_up_by", "notes"}


def create_person(**kw) -> dict[str, Any]:
    now = utcnow()
    data = {k: v for k, v in kw.items() if k in PEOPLE_FIELDS and v is not None}
    data.setdefault("name", "Unnamed contact")
    data.update(created_at=now, updated_at=now)
    return _get("people", _insert("people", data))  # type: ignore[return-value]


def update_person(person_id: int, **kw) -> dict[str, Any] | None:
    data = {k: v for k, v in kw.items() if k in PEOPLE_FIELDS and v is not None}
    data["updated_at"] = utcnow()
    _update("people", person_id, data)
    return _get("people", person_id)


def list_people() -> list[dict[str, Any]]:
    conn = get_connection()
    return rows_to_list(conn.execute("SELECT * FROM people ORDER BY name").fetchall())


# --------------------------------------------------------------------------- #
# Events / calendar
# --------------------------------------------------------------------------- #
EVENT_FIELDS = {
    "title", "starts_at", "ends_at", "location", "kind", "prep_needed",
    "prep_notes", "attendees", "project_id", "user_id",
}


def create_event(**kw) -> dict[str, Any]:
    now = utcnow()
    data = {k: v for k, v in kw.items() if k in EVENT_FIELDS and v is not None}
    data.setdefault("title", "Untitled event")
    data.setdefault("starts_at", now)
    data.update(created_at=now, updated_at=now)
    return _get("events", _insert("events", data))  # type: ignore[return-value]


def update_event(event_id: int, **kw) -> dict[str, Any] | None:
    data = {k: v for k, v in kw.items() if k in EVENT_FIELDS and v is not None}
    data["updated_at"] = utcnow()
    _update("events", event_id, data)
    return _get("events", event_id)


def delete_event(event_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()
    return cur.rowcount > 0


def events_between(start_iso: str, end_iso: str) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM events WHERE starts_at >= ? AND starts_at < ? ORDER BY starts_at ASC",
        (start_iso, end_iso),
    ).fetchall()
    return rows_to_list(rows)


def list_upcoming_events(days: int = 7) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    return events_between(now.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))


def detect_conflicts() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return overlapping upcoming event pairs (Spec 5.7 conflict detection)."""
    events = list_upcoming_events(days=30)
    conflicts = []
    for i in range(len(events)):
        a = events[i]
        a_end = a.get("ends_at") or a["starts_at"]
        for j in range(i + 1, len(events)):
            b = events[j]
            if b["starts_at"] < a_end and b["starts_at"] >= a["starts_at"]:
                conflicts.append((a, b))
    return conflicts


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
DECISION_FIELDS = {
    "title", "objective", "options", "recommendation", "confidence",
    "rationale", "status", "decided_outcome",
}


def create_decision(**kw) -> dict[str, Any]:
    now = utcnow()
    data = {k: v for k, v in kw.items() if k in DECISION_FIELDS and v is not None}
    data.setdefault("title", "Untitled decision")
    data.update(created_at=now, updated_at=now)
    return _get("decisions", _insert("decisions", data))  # type: ignore[return-value]


def update_decision(decision_id: int, **kw) -> dict[str, Any] | None:
    data = {k: v for k, v in kw.items() if k in DECISION_FIELDS and v is not None}
    data["updated_at"] = utcnow()
    _update("decisions", decision_id, data)
    return _get("decisions", decision_id)


def list_decisions(status: str | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    if status:
        rows = conn.execute("SELECT * FROM decisions WHERE status=? ORDER BY updated_at DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM decisions ORDER BY updated_at DESC").fetchall()
    return rows_to_list(rows)


# --------------------------------------------------------------------------- #
# Standing orders
# --------------------------------------------------------------------------- #
def create_standing_order(title: str, instruction: str, autonomy_level: int = 3) -> dict[str, Any]:
    now = utcnow()
    row_id = _insert(
        "standing_orders",
        {
            "title": title,
            "instruction": instruction,
            "autonomy_level": max(0, min(4, autonomy_level)),
            "active": 1,
            "created_at": now,
            "updated_at": now,
        },
    )
    return _get("standing_orders", row_id)  # type: ignore[return-value]


def set_standing_order_active(order_id: int, active: bool) -> dict[str, Any] | None:
    _update("standing_orders", order_id, {"active": 1 if active else 0, "updated_at": utcnow()})
    return _get("standing_orders", order_id)


def list_standing_orders(active_only: bool = True) -> list[dict[str, Any]]:
    conn = get_connection()
    if active_only:
        rows = conn.execute("SELECT * FROM standing_orders WHERE active=1 ORDER BY id").fetchall()
    else:
        rows = conn.execute("SELECT * FROM standing_orders ORDER BY id").fetchall()
    return rows_to_list(rows)


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
MEMORY_FIELDS = {"scope", "category", "content", "status", "outcome", "user_id"}


def add_memory(content: str, scope: str = "long_term", category: str = "note", user_id: int | None = None) -> dict[str, Any]:
    now = utcnow()
    row_id = _insert(
        "memory",
        {
            "scope": scope,
            "category": category,
            "content": content,
            "status": "active",
            "outcome": "",
            "user_id": user_id,
            "created_at": now,
            "updated_at": now,
        },
    )
    return _get("memory", row_id)  # type: ignore[return-value]


def archive_memory(memory_id: int, outcome: str = "") -> dict[str, Any] | None:
    """Archive (never delete) a memory/goal with an outcome note (Spec Memory Scope)."""
    _update("memory", memory_id, {"status": "archived", "outcome": outcome, "updated_at": utcnow()})
    return _get("memory", memory_id)


def list_memory(scope: str | None = None, category: str | None = None, status: str = "active") -> list[dict[str, Any]]:
    conn = get_connection()
    clauses = ["status=?"]
    args: list[Any] = [status]
    if scope:
        clauses.append("scope=?")
        args.append(scope)
    if category:
        clauses.append("category=?")
        args.append(category)
    q = "SELECT * FROM memory WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC"
    return rows_to_list(conn.execute(q, args).fetchall())


def search_memory(term: str) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM memory WHERE content LIKE ? AND status='active' ORDER BY updated_at DESC",
        (f"%{term}%",),
    ).fetchall()
    return rows_to_list(rows)


# --------------------------------------------------------------------------- #
# Messages / conversation log
# --------------------------------------------------------------------------- #
def log_message(role: str, content: str, user_id: int | None = None) -> None:
    _insert("messages", {"role": role, "content": content, "user_id": user_id, "created_at": utcnow()})


def recent_messages(limit: int = 20, user_id: int | None = None) -> list[dict[str, Any]]:
    conn = get_connection()
    if user_id is not None:
        rows = conn.execute(
            "SELECT * FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return list(reversed(rows_to_list(rows)))


def prune_old_messages(retention_days: int) -> int:
    """Delete raw chat logs older than the retention window (Spec: 7 days)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_connection()
    cur = conn.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
    conn.commit()
    return cur.rowcount


def messages_since(iso_dt: str) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM messages WHERE created_at >= ? ORDER BY id ASC", (iso_dt,)
    ).fetchall()
    return rows_to_list(rows)


# --------------------------------------------------------------------------- #
# Action log / audit
# --------------------------------------------------------------------------- #
def log_action(tool: str, summary: str, payload: dict | None = None, autonomy_level: int | None = None, actor: str = "aries") -> None:
    _insert(
        "action_log",
        {
            "actor": actor,
            "tool": tool,
            "summary": summary,
            "payload": json.dumps(payload or {}),
            "autonomy_level": autonomy_level,
            "created_at": utcnow(),
        },
    )


def recent_actions(limit: int = 50) -> list[dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return rows_to_list(rows)
