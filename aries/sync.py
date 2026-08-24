"""Bridges the Google integration to the local operating picture.

Reads are autonomous: pulling calendar events and inbox headers into the local
store keeps briefings and awareness current without any write to the user's
Google account.
"""

from __future__ import annotations

import re
from typing import Any

from . import repository as repo
from .integrations import google


def _categorize(sender: str, subject: str, snippet: str) -> str:
    text = f"{sender} {subject} {snippet}".lower()
    if any(k in text for k in ("invoice", "payment", "receipt", "statement", "renewal", "subscription", "billing")):
        return "financial"
    if any(k in text for k in ("urgent", "asap", "immediately", "action required", "final notice")):
        return "urgent"
    if any(k in text for k in ("meeting", "calendar", "invite", "reschedule", "rsvp")):
        return "scheduling"
    if any(k in text for k in ("newsletter", "unsubscribe", "sale", "% off", "promo", "deal")):
        return "promotional"
    return "general"


def sync_calendar(days_back: int = 1, days_forward: int = 14) -> dict[str, Any]:
    """Import Google Calendar events into the local store (upsert by id)."""
    events = google.fetch_calendar(days_back=days_back, days_forward=days_forward)
    added, updated = 0, 0
    for ev in events:
        if not ev.get("starts_at"):
            continue
        existing = repo.event_by_external_id(ev["external_id"])
        payload = {
            "title": ev["title"],
            "starts_at": ev["starts_at"],
            "ends_at": ev.get("ends_at"),
            "location": ev.get("location", ""),
            "attendees": ev.get("attendees", ""),
            "kind": "meeting",
            "source": "google",
            "external_id": ev["external_id"],
            "external_updated": ev.get("external_updated"),
        }
        if existing:
            repo.update_event(existing["id"], **payload)
            updated += 1
        else:
            repo.create_event(**payload)
            added += 1
    repo.log_action("sync_google_calendar", f"Synced calendar: {added} new, {updated} updated", autonomy_level=0)
    return {"added": added, "updated": updated, "total": len(events)}


def sync_inbox(max_results: int = 25, unread_only: bool = True) -> dict[str, Any]:
    """Import recent Gmail headers into the local store and categorize them."""
    messages = google.fetch_inbox(max_results=max_results, unread_only=unread_only)
    stored = 0
    categories: dict[str, int] = {}
    for m in messages:
        category = _categorize(m.get("sender", ""), m.get("subject", ""), m.get("snippet", ""))
        categories[category] = categories.get(category, 0) + 1
        repo.upsert_email(category=category, **m)
        stored += 1
    repo.log_action("sync_google_inbox", f"Synced {stored} emails", payload=categories, autonomy_level=0)
    return {"synced": stored, "by_category": categories}
