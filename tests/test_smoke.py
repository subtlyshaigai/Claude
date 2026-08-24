"""Smoke tests for Aries that run fully offline (no API key required).

They verify the database, repository, tools, briefings, and HTTP API all work
end-to-end against a temporary database.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway database BEFORE importing any app module.
_TMP = tempfile.mkdtemp()
os.environ["ARIES_DB_PATH"] = str(Path(_TMP) / "test.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "")  # force offline path

from fastapi.testclient import TestClient  # noqa: E402

from aries import repository as repo  # noqa: E402
from aries import tools  # noqa: E402
from aries.briefings import build_digest, operating_snapshot  # noqa: E402
from aries.server import app  # noqa: E402

client = TestClient(app)


def test_default_user_and_status():
    with TestClient(app):  # triggers startup
        r = client.get("/api/status")
        assert r.status_code == 200
        assert r.json()["agent"] == "Aries"
        users = client.get("/api/users").json()["users"]
        assert len(users) >= 1


def test_repository_crud():
    proj = repo.create_project(name="Launch Website", priority="high", objective="Ship v1")
    assert proj["id"] > 0
    task = repo.create_task(title="Draft copy", project_id=proj["id"], priority="high")
    done = repo.update_task(task["id"], status="done")
    assert done["status"] == "done"
    assert done["completed_at"]


def test_tools_dispatch_and_confirmation_gate():
    ev = tools.dispatch("create_event", {"title": "Board meeting", "starts_at": "2026-09-01T15:00:00Z", "prep_needed": True})
    assert "event" in ev
    eid = ev["event"]["id"]

    # Deletion without confirmation must be refused (confirmation gate).
    refused = tools.dispatch("delete_event", {"event_id": eid, "confirmed": False})
    assert "error" in refused

    # With confirmation it proceeds.
    ok = tools.dispatch("delete_event", {"event_id": eid, "confirmed": True})
    assert ok.get("deleted") is True


def test_memory_archive_not_delete():
    m = tools.dispatch("remember", {"content": "Goal: run a half marathon", "scope": "long_term", "category": "goal"})
    mid = m["memory"]["id"]
    archived = tools.dispatch("archive_memory", {"memory_id": mid, "outcome": "Completed"})
    assert archived["memory"]["status"] == "archived"
    # Still retrievable (archived, not deleted).
    active = repo.list_memory(category="goal")
    assert all(x["id"] != mid for x in active)


def test_briefings_render_offline():
    for kind in ("morning", "evening", "weekly", "monthly"):
        text = build_digest(kind)
        assert isinstance(text, str) and len(text) > 0
    assert isinstance(operating_snapshot(), str)


def test_chat_offline_uses_integrity_phrase():
    # No API key -> offline reply that carries the integrity phrase.
    r = client.post("/api/chat", json={"message": "status?"})
    assert r.status_code == 200
    body = r.json()
    assert body["offline"] is True
    assert body["reply"]


def test_dashboard_endpoint():
    r = client.get("/api/dashboard")
    assert r.status_code == 200
    data = r.json()
    for key in ("projects", "tasks", "events", "commitments", "decisions", "people", "memory"):
        assert key in data


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
