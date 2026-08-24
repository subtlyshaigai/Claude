"""Briefing and review generation (Spec sections 7-11).

Two layers:
  * Deterministic digests built purely from the local data — these always work,
    even offline, and are the source of truth.
  * A narration prompt the assistant can use to render a digest in Aries' voice.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import repository as repo


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_when(iso_dt: str | None) -> str:
    if not iso_dt:
        return "no date"
    return iso_dt.replace("T", " ").replace("Z", " UTC")


# --------------------------------------------------------------------------- #
# Compact operating snapshot (fed into the model's context every turn)
# --------------------------------------------------------------------------- #
def operating_snapshot() -> str:
    projects = repo.list_projects()
    tasks = repo.list_tasks()
    commitments = repo.list_commitments("open")
    events = repo.list_upcoming_events(days=7)
    decisions = repo.list_decisions("open")
    orders = repo.list_standing_orders(active_only=True)
    people = repo.list_people()

    now = _now()
    soon = _iso(now + timedelta(days=2))
    overdue = [t for t in tasks if t.get("due") and t["due"] < _iso(now)]
    due_soon = [t for t in tasks if t.get("due") and _iso(now) <= t["due"] <= soon]

    follow_ups = [p for p in people if p.get("follow_up_by") and p["follow_up_by"] <= _iso(now + timedelta(days=3))]

    lines: list[str] = []

    def section(title: str, items: list[str]) -> None:
        lines.append(f"## {title} ({len(items)})")
        if items:
            lines.extend(items[:12])
        else:
            lines.append("  (none)")
        lines.append("")

    section("Active projects", [
        f"  #{p['id']} [{p['priority']}/{p['status']}] {p['name']}"
        + (f" — next: {p['next_action']}" if p.get("next_action") else "")
        + (f" — due {_fmt_when(p['deadline'])}" if p.get("deadline") else "")
        for p in projects
    ])
    section("Open tasks", [
        f"  #{t['id']} [{t['priority']}] {t['title']}"
        + (f" — due {_fmt_when(t['due'])}" if t.get("due") else "")
        + (f" — {t['status']}" if t["status"] != "open" else "")
        for t in tasks
    ])
    if overdue:
        section("OVERDUE tasks", [f"  #{t['id']} {t['title']} (was due {_fmt_when(t['due'])})" for t in overdue])
    if due_soon:
        section("Due within 48h", [f"  #{t['id']} {t['title']} — {_fmt_when(t['due'])}" for t in due_soon])
    section("Upcoming events (7d)", [
        f"  #{e['id']} {_fmt_when(e['starts_at'])} — {e['title']}"
        + (" [prep needed]" if e.get("prep_needed") else "")
        for e in events
    ])
    section("Open commitments", [
        f"  #{c['id']} {c['description']} ({c.get('owed_by','?')}→{c.get('owed_to','?')})"
        + (f" due {_fmt_when(c['due'])}" if c.get("due") else "")
        for c in commitments
    ])
    section("Open decisions", [f"  #{d['id']} {d['title']}" for d in decisions])
    if follow_ups:
        section("People to follow up", [
            f"  #{p['id']} {p['name']} — by {_fmt_when(p['follow_up_by'])}" for p in follow_ups
        ])
    section("Active standing orders", [
        f"  #{o['id']} [L{o['autonomy_level']}] {o['title']}: {o['instruction']}" for o in orders
    ])

    return "\n".join(lines).strip() or "(No data yet. The operating picture is empty.)"


# --------------------------------------------------------------------------- #
# Deterministic digests for the daily / weekly / monthly routines
# --------------------------------------------------------------------------- #
def _risks() -> list[str]:
    now = _now()
    risks: list[str] = []
    for t in repo.list_tasks():
        if t.get("due") and t["due"] < _iso(now):
            risks.append(f"Overdue task #{t['id']}: {t['title']} (was due {_fmt_when(t['due'])})")
    for p in repo.list_projects():
        if p["status"] in ("at_risk", "blocked", "stalled", "overdue"):
            risks.append(f"Project #{p['id']} '{p['name']}' is {p['status']}"
                         + (f" — blocker: {p['blockers']}" if p.get("blockers") else ""))
    for c in repo.list_commitments("open"):
        if c.get("due") and c["due"] < _iso(now):
            risks.append(f"Missed commitment #{c['id']}: {c['description']}")
    for a, b in repo.detect_conflicts():
        risks.append(f"Calendar conflict: '{a['title']}' overlaps '{b['title']}' near {_fmt_when(b['starts_at'])}")
    return risks


def morning_digest() -> str:
    now = _now()
    today_end = _iso(now.replace(hour=23, minute=59, second=59))
    today_start = _iso(now.replace(hour=0, minute=0, second=0))
    todays_events = repo.events_between(today_start, today_end)
    top_tasks = repo.list_tasks()[:7]
    urgent = [t for t in repo.list_tasks() if t["priority"] in ("critical", "high")][:7]
    risks = _risks()

    out = ["# Morning Executive Brief", f"_{now.strftime('%A, %d %B %Y')} (UTC)_", ""]
    out.append("## Today's Schedule")
    out += ([f"- {_fmt_when(e['starts_at'])} — {e['title']}"
             + (" [prep needed]" if e.get("prep_needed") else "") for e in todays_events]
            or ["- No scheduled events."])
    out.append("\n## Priority Items")
    out += ([f"- [{t['priority']}] {t['title']}"
             + (f" (due {_fmt_when(t['due'])})" if t.get("due") else "") for t in urgent]
            or ["- No high-priority items flagged."])
    out.append("\n## Projects Needing Attention")
    attn = [p for p in repo.list_projects() if p["status"] in ("at_risk", "blocked", "stalled", "overdue", "awaiting_principal")]
    out += ([f"- {p['name']} — {p['status']}"
             + (f"; next: {p['next_action']}" if p.get("next_action") else "") for p in attn]
            or ["- All tracked projects on track."])
    out.append("\n## Risks")
    out += ([f"- {r}" for r in risks] or ["- None detected."])
    out.append("\n## Recommendations")
    recs = []
    if risks:
        recs.append("Clear overdue and blocked items first.")
    if any(e.get("prep_needed") for e in todays_events):
        recs.append("Prepare for events flagged [prep needed] before they begin.")
    if not recs:
        recs.append("Protect a focus block for your top priority.")
    out += [f"- {r}" for r in recs]
    return "\n".join(out)


def evening_digest() -> str:
    now = _now()
    day_start = _iso(now.replace(hour=0, minute=0, second=0))
    conn_done = [t for t in repo.list_tasks(include_done=True)
                 if t["status"] == "done" and (t.get("completed_at") or "") >= day_start]
    outstanding = [t for t in repo.list_tasks() if t["priority"] in ("critical", "high")]
    risks = _risks()
    tomorrow = repo.events_between(
        _iso((now + timedelta(days=1)).replace(hour=0, minute=0, second=0)),
        _iso((now + timedelta(days=1)).replace(hour=23, minute=59, second=59)),
    )

    out = ["# Evening Review", f"_{now.strftime('%A, %d %B %Y')} (UTC)_", ""]
    out.append("## Completed Today")
    out += ([f"- {t['title']}" for t in conn_done] or ["- Nothing marked complete today."])
    out.append("\n## Outstanding Priorities")
    out += ([f"- [{t['priority']}] {t['title']}" for t in outstanding] or ["- None outstanding."])
    out.append("\n## Items Requiring Your Attention")
    out += ([f"- {r}" for r in risks] or ["- Nothing pressing."])
    out.append("\n## Tomorrow")
    out += ([f"- {_fmt_when(e['starts_at'])} — {e['title']}" for e in tomorrow] or ["- No events scheduled."])
    return "\n".join(out)


def weekly_digest() -> str:
    now = _now()
    week_ago = _iso(now - timedelta(days=7))
    done = [t for t in repo.list_tasks(include_done=True)
            if t["status"] == "done" and (t.get("completed_at") or "") >= week_ago]
    projects = repo.list_projects(include_closed=False)
    by_status: dict[str, list[str]] = {}
    for p in projects:
        by_status.setdefault(p["status"], []).append(p["name"])
    open_commitments = repo.list_commitments("open")
    follow_ups = [p for p in repo.list_people() if p.get("follow_up_by")]
    decisions = repo.list_decisions("open")

    out = ["# Executive Weekly Review", f"_Week ending {now.strftime('%d %B %Y')} (UTC)_", ""]
    out.append("## Accomplishments")
    out += ([f"- {t['title']}" for t in done] or ["- No completed tasks recorded this week."])
    out.append("\n## Projects by Status")
    out += ([f"- **{s.replace('_',' ')}**: {', '.join(names)}" for s, names in by_status.items()]
            or ["- No active projects."])
    out.append("\n## Open Commitments")
    out += ([f"- {c['description']}"
             + (f" (due {_fmt_when(c['due'])})" if c.get("due") else "") for c in open_commitments]
            or ["- None open."])
    out.append("\n## Follow-ups Needed")
    out += ([f"- {p['name']}"
             + (f" by {_fmt_when(p['follow_up_by'])}" if p.get("follow_up_by") else "") for p in follow_ups]
            or ["- None flagged."])
    out.append("\n## Decisions Awaiting You")
    out += ([f"- {d['title']}" for d in decisions] or ["- None pending."])
    out.append("\n## Risks")
    out += ([f"- {r}" for r in _risks()] or ["- None detected."])
    return "\n".join(out)


def monthly_digest() -> str:
    now = _now()
    goals = repo.list_memory(category="goal")
    businesses = repo.list_memory(category="business")
    projects = repo.list_projects(include_closed=True)
    completed = [p for p in projects if p["status"] == "complete"]
    delayed = [p for p in projects if p["status"] in ("overdue", "stalled", "at_risk")]

    out = ["# Monthly Executive Review", f"_{now.strftime('%B %Y')} (UTC)_", ""]
    out.append("## Goals")
    out += ([f"- {g['content']}" for g in goals] or ["- No goals recorded. Consider defining a few."])
    out.append("\n## Businesses")
    out += ([f"- {b['content']}" for b in businesses] or ["- No business notes recorded."])
    out.append("\n## Projects — Completed")
    out += ([f"- {p['name']}" for p in completed] or ["- None completed this period."])
    out.append("\n## Projects — Needing Intervention")
    out += ([f"- {p['name']} ({p['status']})" for p in delayed] or ["- None flagged."])
    out.append("\n## Relationships")
    out += ([f"- {p['name']}"
             + (f" — {p['relationship']}" if p.get("relationship") else "")
             for p in repo.list_people()] or ["- No relationships tracked."])
    out.append("\n## Strategic Recommendations")
    out.append("_If we changed only three things next month, what should they be?_ "
               "Aries will propose these interactively based on the above.")
    return "\n".join(out)


DIGESTS = {
    "morning": morning_digest,
    "evening": evening_digest,
    "weekly": weekly_digest,
    "monthly": monthly_digest,
}


def build_digest(kind: str) -> str:
    fn = DIGESTS.get(kind)
    if not fn:
        raise ValueError(f"Unknown briefing kind: {kind}")
    return fn()
