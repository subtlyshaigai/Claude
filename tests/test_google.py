"""Google integration tests. No real network or credentials: the provider calls
are monkeypatched so the sync layer, categorization, and confirmation gates can
be verified deterministically.
"""

from aries import repository as repo
from aries import sync
from aries import tools
from aries.integrations import google


def test_connection_status_shape():
    st = google.connection_status()
    for key in ("libs_available", "configured", "connected", "redirect_uri", "scopes"):
        assert key in st
    # Not configured in the test env.
    assert st["configured"] is False
    assert st["connected"] is False


def test_categorize():
    assert sync._categorize("billing@x.com", "Your invoice", "amount due") == "financial"
    assert sync._categorize("a@b.com", "URGENT: action required", "") == "urgent"
    assert sync._categorize("a@b.com", "Meeting invite", "rsvp") == "scheduling"
    assert sync._categorize("news@shop.com", "50% off sale", "unsubscribe") == "promotional"
    assert sync._categorize("friend@x.com", "hello", "how are you") == "general"


def test_sync_calendar_imports_events(monkeypatch):
    fake = [
        {"external_id": "evt-1", "title": "Board meeting", "starts_at": "2099-01-01T10:00:00Z",
         "ends_at": "2099-01-01T11:00:00Z", "location": "HQ", "attendees": "a@b.com", "external_updated": "x"},
    ]
    monkeypatch.setattr(google, "is_connected", lambda: True)
    monkeypatch.setattr(google, "fetch_calendar", lambda **kw: fake)
    res = sync.sync_calendar()
    assert res["added"] >= 1
    stored = repo.event_by_external_id("evt-1")
    assert stored and stored["source"] == "google" and stored["title"] == "Board meeting"

    # Re-sync updates rather than duplicates.
    res2 = sync.sync_calendar()
    assert res2["updated"] >= 1


def test_sync_inbox_categorizes(monkeypatch):
    fake = [
        {"external_id": "m-1", "thread_id": "t-1", "sender": "billing@x.com",
         "recipient": "me@x.com", "subject": "Invoice #12", "snippet": "payment due",
         "received_at": "2099-01-01T09:00:00Z", "is_unread": 1},
    ]
    monkeypatch.setattr(google, "is_connected", lambda: True)
    monkeypatch.setattr(google, "fetch_inbox", lambda **kw: fake)
    res = sync.sync_inbox()
    assert res["synced"] == 1
    emails = repo.list_emails()
    assert any(e["external_id"] == "m-1" and e["category"] == "financial" for e in emails)


def test_send_email_confirmation_gate():
    # Refused without confirmation, before any connection is required.
    refused = tools.dispatch("send_email", {"to": "x@y.com", "subject": "Hi", "body": "hello", "confirmed": False})
    assert "error" in refused and "confirmation" in refused["error"].lower()

    # Confirmed but not connected -> reports unavailable, still does not send.
    not_connected = tools.dispatch("send_email", {"to": "x@y.com", "subject": "Hi", "body": "hello", "confirmed": True})
    assert "error" in not_connected


def test_push_event_confirmation_gate():
    refused = tools.dispatch("push_event_to_google", {"title": "X", "starts_at": "2099-01-01T10:00:00Z", "confirmed": False})
    assert "error" in refused and "confirmation" in refused["error"].lower()


def test_sync_tool_reports_when_disconnected():
    # is_connected is False in the test env -> tool returns a clean error, no raise.
    out = tools.dispatch("sync_google_calendar", {})
    assert "error" in out
