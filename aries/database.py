"""SQLite persistence for Aries.

A single local database file holds the entire operating picture: people,
projects, tasks, commitments, calendar events, decisions, standing orders,
memory, and conversation logs. Everything stays on the host machine.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings


def utcnow() -> str:
    """ISO-8601 UTC timestamp used consistently as the storage format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA = """
-- Family members and the Principal. `role` is 'principal' or 'family'.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'family',
    preferences   TEXT NOT NULL DEFAULT '{}',   -- JSON blob of personalization
    password_hash TEXT NOT NULL DEFAULT '',      -- scrypt hash; empty = no password set
    password_salt TEXT NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    UNIQUE(name)
);

-- Browser login sessions (Spec 25: authority tied to identity).
CREATE TABLE IF NOT EXISTS sessions (
    token         TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Projects / businesses / ventures tracked by Aries (Spec 5.3, 5.4).
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'project',  -- project | business | goal
    objective     TEXT DEFAULT '',
    outcome       TEXT DEFAULT '',
    owner         TEXT DEFAULT '',
    stakeholders  TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'on_track', -- on_track|at_risk|blocked|stalled|overdue|awaiting_principal|awaiting_other|complete|shelved
    priority      TEXT NOT NULL DEFAULT 'medium',   -- critical|high|medium|low
    deadline      TEXT,
    milestones    TEXT DEFAULT '',
    dependencies  TEXT DEFAULT '',
    risks         TEXT DEFAULT '',
    blockers      TEXT DEFAULT '',
    next_action   TEXT DEFAULT '',
    notes         TEXT DEFAULT '',
    user_id       INTEGER,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Actionable tasks, reminders, errands, action items (Spec 5.1, 5.2).
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    detail        TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'open',     -- open|in_progress|blocked|done|dropped
    priority      TEXT NOT NULL DEFAULT 'medium',   -- critical|high|medium|low
    category      TEXT DEFAULT 'general',           -- general|errand|admin|followup|recurring|prep
    due           TEXT,
    project_id    INTEGER,
    user_id       INTEGER,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    completed_at  TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Commitments made by, or to, the Principal (Spec 20, 21).
CREATE TABLE IF NOT EXISTS commitments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    description   TEXT NOT NULL,
    owed_by       TEXT DEFAULT 'principal',         -- who owes it
    owed_to       TEXT DEFAULT '',                  -- to whom
    due           TEXT,
    status        TEXT NOT NULL DEFAULT 'open',     -- open|kept|missed|renegotiated|dropped
    project_id    INTEGER,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

-- People / relationships requiring follow-up (Spec 5.6, monthly review).
CREATE TABLE IF NOT EXISTS people (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    relationship  TEXT DEFAULT '',
    context       TEXT DEFAULT '',
    last_contact  TEXT,
    follow_up_by  TEXT,
    notes         TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Calendar / schedule events (Spec 5.7).
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    starts_at     TEXT NOT NULL,
    ends_at       TEXT,
    location      TEXT DEFAULT '',
    kind          TEXT DEFAULT 'appointment',       -- meeting|appointment|travel|focus|personal
    prep_needed   INTEGER NOT NULL DEFAULT 0,
    prep_notes    TEXT DEFAULT '',
    attendees     TEXT DEFAULT '',
    project_id    INTEGER,
    user_id       INTEGER,
    source        TEXT NOT NULL DEFAULT 'local',   -- local | google | proposed
    external_id   TEXT,                            -- provider event id when synced
    external_updated TEXT,                         -- provider's last-updated timestamp
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Emails pulled in for awareness (Spec 5.6). Read + propose: bodies summarized,
-- drafts prepared, but sending is gated. Pruned with the chat retention policy.
CREATE TABLE IF NOT EXISTS emails (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id   TEXT,                            -- provider message id
    thread_id     TEXT,
    sender        TEXT DEFAULT '',
    recipient     TEXT DEFAULT '',
    subject       TEXT DEFAULT '',
    snippet       TEXT DEFAULT '',
    received_at   TEXT,
    is_unread     INTEGER NOT NULL DEFAULT 1,
    category      TEXT DEFAULT 'uncategorized',
    source        TEXT NOT NULL DEFAULT 'google',
    created_at    TEXT NOT NULL,
    UNIQUE(external_id)
);

-- Decision log & decision briefs (Spec 12, 20).
CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    objective     TEXT DEFAULT '',
    options       TEXT DEFAULT '',
    recommendation TEXT DEFAULT '',
    confidence    TEXT DEFAULT '',
    rationale     TEXT DEFAULT '',                  -- why; alternatives; assumptions
    status        TEXT NOT NULL DEFAULT 'open',     -- open|decided|deferred
    decided_outcome TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Standing orders: explicit authorizations that raise Aries' autonomy (Spec 6, 25).
CREATE TABLE IF NOT EXISTS standing_orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    instruction   TEXT NOT NULL,
    autonomy_level INTEGER NOT NULL DEFAULT 3,      -- 0..4 per the autonomy model
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Long-term memory: goals, preferences, business facts (Spec 20, Memory Scope).
-- scope: short_term | long_term | permanent
CREATE TABLE IF NOT EXISTS memory (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    scope         TEXT NOT NULL DEFAULT 'long_term',
    category      TEXT NOT NULL DEFAULT 'note',     -- goal|preference|business|decision|person|note
    content       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',   -- active|archived
    outcome       TEXT DEFAULT '',                  -- outcome note for archived goals
    user_id       INTEGER,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Raw conversation log. Pruned per retention policy (Spec Memory Scope: 7 days).
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER,
    role          TEXT NOT NULL,                    -- user | assistant | system
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- Audit trail of every action Aries takes on the data (Spec 16 VERIFY/REPORT).
CREATE TABLE IF NOT EXISTS action_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    actor         TEXT NOT NULL DEFAULT 'aries',
    tool          TEXT NOT NULL,
    summary       TEXT NOT NULL,
    payload       TEXT DEFAULT '{}',
    autonomy_level INTEGER,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(starts_at);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_memory_scope ON memory(scope);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received_at);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
"""


# Columns added after the first release. Applied idempotently so databases
# created by an earlier version pick them up without a manual migration.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "users": {
        "password_hash": "TEXT NOT NULL DEFAULT ''",
        "password_salt": "TEXT NOT NULL DEFAULT ''",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
    },
    "events": {
        "source": "TEXT NOT NULL DEFAULT 'local'",
        "external_id": "TEXT",
        "external_updated": "TEXT",
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


_connection: sqlite3.Connection | None = None


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Return a process-wide SQLite connection, creating it (and the schema) on
    first use. ``check_same_thread=False`` because FastAPI serves requests from
    a thread pool; writes are naturally serialized by SQLite's file lock and by
    the short-lived nature of each call."""
    global _connection
    if _connection is not None and db_path is None:
        return _connection

    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    if db_path is None:
        _connection = conn
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
