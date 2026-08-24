"""Tool schemas exposed to Claude, plus the dispatcher that executes them
against the local store. Destructive actions enforce the spec's confirmation
gate at the code level: they refuse unless ``confirmed`` is explicitly true.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from . import repository as repo

# --------------------------------------------------------------------------- #
# Tool JSON schemas (Anthropic tool-use format)
# --------------------------------------------------------------------------- #
_PRIORITY = {"type": "string", "enum": ["critical", "high", "medium", "low"]}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "get_operating_picture",
        "description": "Read a compact snapshot of the entire operating picture: "
        "active projects, open tasks, upcoming events, open commitments, open "
        "decisions, follow-ups, and standing orders. Call this before giving "
        "status, planning, or briefing.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---- Projects -------------------------------------------------------- #
    {
        "name": "create_project",
        "description": "Create a project, business, or goal to track.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["project", "business", "goal"]},
                "objective": {"type": "string"},
                "outcome": {"type": "string", "description": "Desired outcome"},
                "owner": {"type": "string"},
                "stakeholders": {"type": "string"},
                "status": {"type": "string", "enum": [
                    "on_track", "at_risk", "blocked", "stalled", "overdue",
                    "awaiting_principal", "awaiting_other", "complete", "shelved"]},
                "priority": _PRIORITY,
                "deadline": {"type": "string", "description": "ISO date/datetime"},
                "risks": {"type": "string"},
                "blockers": {"type": "string"},
                "next_action": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_project",
        "description": "Update fields on an existing project by id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "status": {"type": "string"},
                "priority": _PRIORITY,
                "objective": {"type": "string"},
                "outcome": {"type": "string"},
                "deadline": {"type": "string"},
                "risks": {"type": "string"},
                "blockers": {"type": "string"},
                "next_action": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "list_projects",
        "description": "List projects, optionally filtered by status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "include_closed": {"type": "boolean"},
            },
        },
    },
    # ---- Tasks ----------------------------------------------------------- #
    {
        "name": "create_task",
        "description": "Create a task, reminder, errand, or action item.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "detail": {"type": "string"},
                "priority": _PRIORITY,
                "category": {"type": "string", "enum": [
                    "general", "errand", "admin", "followup", "recurring", "prep"]},
                "due": {"type": "string", "description": "ISO date/datetime"},
                "project_id": {"type": "integer"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_task",
        "description": "Update a task by id. Set status to 'done' to complete it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title": {"type": "string"},
                "detail": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "in_progress", "blocked", "done", "dropped"]},
                "priority": _PRIORITY,
                "due": {"type": "string"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List tasks, optionally filtered by status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "include_done": {"type": "boolean"},
            },
        },
    },
    # ---- Commitments ----------------------------------------------------- #
    {
        "name": "create_commitment",
        "description": "Record a commitment made by or to the Principal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "owed_by": {"type": "string", "description": "'principal' or a person's name"},
                "owed_to": {"type": "string"},
                "due": {"type": "string"},
                "project_id": {"type": "integer"},
            },
            "required": ["description"],
        },
    },
    {
        "name": "update_commitment",
        "description": "Update a commitment by id (e.g. mark kept, missed, renegotiated).",
        "input_schema": {
            "type": "object",
            "properties": {
                "commitment_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["open", "kept", "missed", "renegotiated", "dropped"]},
                "due": {"type": "string"},
            },
            "required": ["commitment_id"],
        },
    },
    {
        "name": "list_commitments",
        "description": "List commitments, default only open ones.",
        "input_schema": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
        },
    },
    # ---- People ---------------------------------------------------------- #
    {
        "name": "add_person",
        "description": "Add a person/relationship to track for follow-up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "relationship": {"type": "string"},
                "context": {"type": "string"},
                "last_contact": {"type": "string"},
                "follow_up_by": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_person",
        "description": "Update a tracked person by id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "person_id": {"type": "integer"},
                "relationship": {"type": "string"},
                "context": {"type": "string"},
                "last_contact": {"type": "string"},
                "follow_up_by": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["person_id"],
        },
    },
    {"name": "list_people", "description": "List tracked people.", "input_schema": {"type": "object", "properties": {}}},
    # ---- Events ---------------------------------------------------------- #
    {
        "name": "create_event",
        "description": "Add a calendar event (meeting, appointment, travel, focus block, personal).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "starts_at": {"type": "string", "description": "ISO datetime"},
                "ends_at": {"type": "string"},
                "location": {"type": "string"},
                "kind": {"type": "string", "enum": ["meeting", "appointment", "travel", "focus", "personal"]},
                "prep_needed": {"type": "boolean"},
                "prep_notes": {"type": "string"},
                "attendees": {"type": "string"},
                "project_id": {"type": "integer"},
            },
            "required": ["title", "starts_at"],
        },
    },
    {
        "name": "update_event",
        "description": "Update a calendar event by id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "integer"},
                "title": {"type": "string"},
                "starts_at": {"type": "string"},
                "ends_at": {"type": "string"},
                "location": {"type": "string"},
                "prep_needed": {"type": "boolean"},
                "prep_notes": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_event",
        "description": "Delete a calendar event. This is destructive and IRREVERSIBLE. "
        "You must first confirm with the current user and only then call this "
        "with confirmed=true. Never delete without explicit confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "integer"},
                "confirmed": {"type": "boolean", "description": "Must be true; set only after the user explicitly approves."},
            },
            "required": ["event_id", "confirmed"],
        },
    },
    {
        "name": "list_events",
        "description": "List upcoming calendar events within N days (default 7). Also reports conflicts.",
        "input_schema": {
            "type": "object",
            "properties": {"days": {"type": "integer"}},
        },
    },
    # ---- Decisions ------------------------------------------------------- #
    {
        "name": "create_decision",
        "description": "Record a decision or a decision brief (options, recommendation, confidence).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "objective": {"type": "string"},
                "options": {"type": "string"},
                "recommendation": {"type": "string"},
                "confidence": {"type": "string"},
                "rationale": {"type": "string", "description": "Why; alternatives considered; assumptions"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_decision",
        "description": "Update a decision, e.g. record the Principal's chosen outcome.",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision_id": {"type": "integer"},
                "status": {"type": "string", "enum": ["open", "decided", "deferred"]},
                "decided_outcome": {"type": "string"},
                "recommendation": {"type": "string"},
                "confidence": {"type": "string"},
            },
            "required": ["decision_id"],
        },
    },
    {
        "name": "list_decisions",
        "description": "List logged decisions, optionally by status.",
        "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}},
    },
    # ---- Standing orders ------------------------------------------------- #
    {
        "name": "add_standing_order",
        "description": "Record an explicit standing order that authorizes autonomous action "
        "at a given autonomy level (0-4). Only create when the Principal explicitly grants it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "instruction": {"type": "string"},
                "autonomy_level": {"type": "integer", "minimum": 0, "maximum": 4},
            },
            "required": ["title", "instruction"],
        },
    },
    {
        "name": "list_standing_orders",
        "description": "List active standing orders.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---- Memory ---------------------------------------------------------- #
    {
        "name": "remember",
        "description": "Persist something to memory: a goal, preference, business fact, or note. "
        "Use scope 'permanent' for business ideas/info, 'long_term' for goals/decisions/"
        "preferences, 'short_term' for transient context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "scope": {"type": "string", "enum": ["short_term", "long_term", "permanent"]},
                "category": {"type": "string", "enum": [
                    "goal", "preference", "business", "decision", "person", "note"]},
            },
            "required": ["content"],
        },
    },
    {
        "name": "archive_memory",
        "description": "Archive (NOT delete) a memory or completed goal with an outcome note. "
        "Retained for pattern learning; purge only on explicit user command.",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer"},
                "outcome": {"type": "string"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "search_memory",
        "description": "Search long-term/permanent memory for a term.",
        "input_schema": {
            "type": "object",
            "properties": {"term": {"type": "string"}},
            "required": ["term"],
        },
    },
    {
        "name": "list_memory",
        "description": "List memory entries, optionally by scope and category.",
        "input_schema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "category": {"type": "string"},
            },
        },
    },
    # ---- Google integration (read = autonomous, write = gated) ----------- #
    {
        "name": "sync_google_calendar",
        "description": "Pull the user's Google Calendar into the local operating "
        "picture (read-only, autonomous). Call before scheduling questions or "
        "briefings when a Google account is connected.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_back": {"type": "integer"},
                "days_forward": {"type": "integer"},
            },
        },
    },
    {
        "name": "sync_google_inbox",
        "description": "Pull recent Gmail headers/snippets into the local store and "
        "categorize them (read-only, autonomous). Use for communications triage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer"},
                "unread_only": {"type": "boolean"},
            },
        },
    },
    {
        "name": "list_emails",
        "description": "List locally-synced emails (read-only), optionally unread only.",
        "input_schema": {
            "type": "object",
            "properties": {"unread_only": {"type": "boolean"}},
        },
    },
    {
        "name": "draft_email",
        "description": "Prepare a Gmail DRAFT (Level 2 preparation). This does NOT send. "
        "Use to stage a reply for the Principal to review and send.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email via Gmail. GATED (Level 4): consequential and hard to "
        "undo. First state to the user exactly what will be sent and to whom, get "
        "explicit approval, then call again with confirmed=true. Never send without it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "confirmed": {"type": "boolean", "description": "Must be true; set only after explicit approval."},
            },
            "required": ["to", "subject", "body", "confirmed"],
        },
    },
    {
        "name": "push_event_to_google",
        "description": "Create an event on the user's real Google Calendar. GATED (Level 4). "
        "Confirm the details with the user first, then call with confirmed=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "starts_at": {"type": "string", "description": "ISO datetime"},
                "ends_at": {"type": "string"},
                "location": {"type": "string"},
                "description": {"type": "string"},
                "confirmed": {"type": "boolean", "description": "Must be true; set only after explicit approval."},
            },
            "required": ["title", "starts_at", "confirmed"],
        },
    },
]


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
# Read-only tools: autonomous (Level 0), no audit row from the dispatcher.
_READ_ONLY = {
    "sync_google_calendar", "sync_google_inbox",  # read Google -> local; self-logged
}
# Gated tools that require explicit confirmation (Level 4 Confirm).
_GATED = {"delete_event", "send_email", "push_event_to_google"}


def _is_read(tool_name: str) -> bool:
    return tool_name.startswith(("list_", "get_", "search_")) or tool_name in _READ_ONLY


def _autonomy_for(tool_name: str) -> int:
    """Best-effort mapping of a tool to the autonomy level it represents, for
    the audit log. Reads are 0; gated writes are Level 4; drafts are Level 2;
    ordinary local writes are effectively Level 3 execute."""
    if _is_read(tool_name):
        return 0
    if tool_name in _GATED:
        return 4
    if tool_name == "draft_email":
        return 2
    return 3


def dispatch(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call and return a JSON-serializable result dict. Never
    raises for expected error conditions; returns {"error": ...} instead so the
    model can recover."""
    try:
        result = _DISPATCH[tool_name](args)
    except KeyError:
        return {"error": f"Unknown tool: {tool_name}"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"error": f"{type(exc).__name__}: {exc}"}

    # Audit every mutating call (reads and self-logging syncs excluded).
    if not _is_read(tool_name) and "error" not in result:
        repo.log_action(
            tool=tool_name,
            summary=result.get("_summary", tool_name),
            payload={k: v for k, v in args.items() if k != "body"},  # keep email bodies out of the log
            autonomy_level=_autonomy_for(tool_name),
        )
    result.pop("_summary", None)
    return result


def _h_get_operating_picture(_: dict) -> dict:
    from .briefings import operating_snapshot  # local import avoids a cycle
    return {"snapshot": operating_snapshot()}


def _h_create_project(a: dict) -> dict:
    p = repo.create_project(**a)
    return {"project": p, "_summary": f"Created project '{p['name']}' (#{p['id']})"}


def _h_update_project(a: dict) -> dict:
    pid = a.pop("project_id")
    p = repo.update_project(pid, **a)
    if not p:
        return {"error": f"No project with id {pid}"}
    return {"project": p, "_summary": f"Updated project #{pid}"}


def _h_list_projects(a: dict) -> dict:
    return {"projects": repo.list_projects(status=a.get("status"), include_closed=a.get("include_closed", False))}


def _h_create_task(a: dict) -> dict:
    t = repo.create_task(**a)
    return {"task": t, "_summary": f"Created task '{t['title']}' (#{t['id']})"}


def _h_update_task(a: dict) -> dict:
    tid = a.pop("task_id")
    t = repo.update_task(tid, **a)
    if not t:
        return {"error": f"No task with id {tid}"}
    return {"task": t, "_summary": f"Updated task #{tid} -> {t['status']}"}


def _h_list_tasks(a: dict) -> dict:
    return {"tasks": repo.list_tasks(status=a.get("status"), include_done=a.get("include_done", False))}


def _h_create_commitment(a: dict) -> dict:
    c = repo.create_commitment(**a)
    return {"commitment": c, "_summary": f"Recorded commitment #{c['id']}"}


def _h_update_commitment(a: dict) -> dict:
    cid = a.pop("commitment_id")
    c = repo.update_commitment(cid, **a)
    if not c:
        return {"error": f"No commitment with id {cid}"}
    return {"commitment": c, "_summary": f"Updated commitment #{cid}"}


def _h_list_commitments(a: dict) -> dict:
    return {"commitments": repo.list_commitments(status=a.get("status", "open"))}


def _h_add_person(a: dict) -> dict:
    p = repo.create_person(**a)
    return {"person": p, "_summary": f"Added person '{p['name']}' (#{p['id']})"}


def _h_update_person(a: dict) -> dict:
    pid = a.pop("person_id")
    p = repo.update_person(pid, **a)
    if not p:
        return {"error": f"No person with id {pid}"}
    return {"person": p, "_summary": f"Updated person #{pid}"}


def _h_list_people(_: dict) -> dict:
    return {"people": repo.list_people()}


def _h_create_event(a: dict) -> dict:
    if isinstance(a.get("prep_needed"), bool):
        a["prep_needed"] = 1 if a["prep_needed"] else 0
    e = repo.create_event(**a)
    return {"event": e, "_summary": f"Added event '{e['title']}' at {e['starts_at']}"}


def _h_update_event(a: dict) -> dict:
    eid = a.pop("event_id")
    if isinstance(a.get("prep_needed"), bool):
        a["prep_needed"] = 1 if a["prep_needed"] else 0
    e = repo.update_event(eid, **a)
    if not e:
        return {"error": f"No event with id {eid}"}
    return {"event": e, "_summary": f"Updated event #{eid}"}


def _h_delete_event(a: dict) -> dict:
    # Confirmation gate enforced in code, per the spec.
    if not a.get("confirmed"):
        return {"error": "Deletion refused: confirmation gate. Ask the user to confirm, "
                         "then call again with confirmed=true."}
    eid = a["event_id"]
    ok = repo.delete_event(eid)
    if not ok:
        return {"error": f"No event with id {eid}"}
    return {"deleted": True, "_summary": f"Deleted event #{eid} (confirmed)"}


def _h_list_events(a: dict) -> dict:
    days = a.get("days", 7)
    events = repo.list_upcoming_events(days=days)
    conflicts = [
        {"a": {"id": x["id"], "title": x["title"], "starts_at": x["starts_at"]},
         "b": {"id": y["id"], "title": y["title"], "starts_at": y["starts_at"]}}
        for x, y in repo.detect_conflicts()
    ]
    return {"events": events, "conflicts": conflicts}


def _h_create_decision(a: dict) -> dict:
    d = repo.create_decision(**a)
    return {"decision": d, "_summary": f"Logged decision '{d['title']}' (#{d['id']})"}


def _h_update_decision(a: dict) -> dict:
    did = a.pop("decision_id")
    d = repo.update_decision(did, **a)
    if not d:
        return {"error": f"No decision with id {did}"}
    return {"decision": d, "_summary": f"Updated decision #{did}"}


def _h_list_decisions(a: dict) -> dict:
    return {"decisions": repo.list_decisions(status=a.get("status"))}


def _h_add_standing_order(a: dict) -> dict:
    o = repo.create_standing_order(
        title=a["title"], instruction=a["instruction"], autonomy_level=a.get("autonomy_level", 3)
    )
    return {"standing_order": o, "_summary": f"Added standing order '{o['title']}' (L{o['autonomy_level']})"}


def _h_list_standing_orders(_: dict) -> dict:
    return {"standing_orders": repo.list_standing_orders(active_only=True)}


def _h_remember(a: dict) -> dict:
    m = repo.add_memory(content=a["content"], scope=a.get("scope", "long_term"), category=a.get("category", "note"))
    return {"memory": m, "_summary": f"Remembered [{m['scope']}/{m['category']}] #{m['id']}"}


def _h_archive_memory(a: dict) -> dict:
    m = repo.archive_memory(a["memory_id"], outcome=a.get("outcome", ""))
    if not m:
        return {"error": f"No memory with id {a['memory_id']}"}
    return {"memory": m, "_summary": f"Archived memory #{m['id']}"}


def _h_search_memory(a: dict) -> dict:
    return {"results": repo.search_memory(a["term"])}


def _h_list_memory(a: dict) -> dict:
    return {"memory": repo.list_memory(scope=a.get("scope"), category=a.get("category"))}


# ---- Google handlers ------------------------------------------------------ #
def _google_guard():
    from .integrations import google
    if not google.is_connected():
        raise google.GoogleUnavailable(
            "No Google account is connected. Ask the Principal to connect one in the "
            "Integrations panel."
        )


def _h_sync_google_calendar(a: dict) -> dict:
    from . import sync
    from .integrations.google import GoogleUnavailable
    try:
        _google_guard()
        res = sync.sync_calendar(days_back=a.get("days_back", 1), days_forward=a.get("days_forward", 14))
    except GoogleUnavailable as exc:
        return {"error": str(exc)}
    return {"result": res, "_summary": f"Synced Google Calendar ({res['added']} new, {res['updated']} updated)"}


def _h_sync_google_inbox(a: dict) -> dict:
    from . import sync
    from .integrations.google import GoogleUnavailable
    try:
        _google_guard()
        res = sync.sync_inbox(max_results=a.get("max_results", 25), unread_only=a.get("unread_only", True))
    except GoogleUnavailable as exc:
        return {"error": str(exc)}
    return {"result": res, "_summary": f"Synced Gmail ({res['synced']} messages)"}


def _h_list_emails(a: dict) -> dict:
    return {"emails": repo.list_emails(unread_only=a.get("unread_only", False))}


def _h_draft_email(a: dict) -> dict:
    from .integrations import google
    try:
        _google_guard()
        res = google.create_draft(a["to"], a["subject"], a["body"])
    except google.GoogleUnavailable as exc:
        return {"error": str(exc)}
    return {"draft": res, "_summary": f"Drafted email to {a['to']} (not sent)"}


def _h_send_email(a: dict) -> dict:
    from .integrations import google
    if not a.get("confirmed"):
        return {"error": "Send refused: confirmation gate. State the recipient, subject, and "
                         "body to the user, obtain approval, then call again with confirmed=true."}
    try:
        _google_guard()
        res = google.send_email(a["to"], a["subject"], a["body"])
    except google.GoogleUnavailable as exc:
        return {"error": str(exc)}
    return {"sent": res, "_summary": f"Sent email to {a['to']} (confirmed)"}


def _h_push_event_to_google(a: dict) -> dict:
    from .integrations import google
    if not a.get("confirmed"):
        return {"error": "Push refused: confirmation gate. Confirm the event details with the "
                         "user, then call again with confirmed=true."}
    try:
        _google_guard()
        res = google.push_event(
            title=a["title"], starts_at=a["starts_at"], ends_at=a.get("ends_at"),
            location=a.get("location", ""), description=a.get("description", ""),
        )
    except google.GoogleUnavailable as exc:
        return {"error": str(exc)}
    # Mirror the pushed event locally so it appears immediately.
    if res.get("external_id"):
        repo.create_event(
            title=a["title"], starts_at=a["starts_at"], ends_at=a.get("ends_at"),
            location=a.get("location", ""), source="google", external_id=res["external_id"],
        )
    return {"event": res, "_summary": f"Created Google Calendar event '{a['title']}' (confirmed)"}


_DISPATCH: dict[str, Callable[[dict], dict]] = {
    "get_operating_picture": _h_get_operating_picture,
    "create_project": _h_create_project,
    "update_project": _h_update_project,
    "list_projects": _h_list_projects,
    "create_task": _h_create_task,
    "update_task": _h_update_task,
    "list_tasks": _h_list_tasks,
    "create_commitment": _h_create_commitment,
    "update_commitment": _h_update_commitment,
    "list_commitments": _h_list_commitments,
    "add_person": _h_add_person,
    "update_person": _h_update_person,
    "list_people": _h_list_people,
    "create_event": _h_create_event,
    "update_event": _h_update_event,
    "delete_event": _h_delete_event,
    "list_events": _h_list_events,
    "create_decision": _h_create_decision,
    "update_decision": _h_update_decision,
    "list_decisions": _h_list_decisions,
    "add_standing_order": _h_add_standing_order,
    "list_standing_orders": _h_list_standing_orders,
    "remember": _h_remember,
    "archive_memory": _h_archive_memory,
    "search_memory": _h_search_memory,
    "list_memory": _h_list_memory,
    "sync_google_calendar": _h_sync_google_calendar,
    "sync_google_inbox": _h_sync_google_inbox,
    "list_emails": _h_list_emails,
    "draft_email": _h_draft_email,
    "send_email": _h_send_email,
    "push_event_to_google": _h_push_event_to_google,
}
