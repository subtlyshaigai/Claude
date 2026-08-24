"""FastAPI application: serves the local web UI and the JSON API that both the
UI and any household device use to talk to Aries.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import assistant as assistant_mod
from . import repository as repo
from .briefings import operating_snapshot
from .config import settings
from .database import get_connection

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_connection()  # create schema
    repo.ensure_default_user()
    # Enforce the 7-day raw-chat retention policy on each start (Spec Memory Scope).
    pruned = repo.prune_old_messages(settings.chatlog_retention_days)
    if pruned:
        repo.log_action("maintenance", f"Pruned {pruned} chat log rows past retention", autonomy_level=0)
    yield


app = FastAPI(title="Aries — Executive Chief of Staff", version=__version__, lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class ChatIn(BaseModel):
    message: str
    user_id: Optional[int] = None


class UserIn(BaseModel):
    name: str
    role: str = "family"


class GenericIn(BaseModel):
    """Loose passthrough for create/update dashboard actions."""
    class Config:
        extra = "allow"


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #
@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "agent": "Aries",
        "designation": "Constellation01",
        "version": __version__,
        "llm_enabled": settings.llm_enabled,
        "model": settings.model if settings.llm_enabled else None,
        "integrity_phrase_set": bool(settings.integrity_phrase),
        "chatlog_retention_days": settings.chatlog_retention_days,
    }


def _resolve_user(user_id: int | None) -> dict[str, Any]:
    if user_id is not None:
        u = repo.get_user(user_id)
        if u:
            return u
    return repo.ensure_default_user()


# --------------------------------------------------------------------------- #
# Users / family
# --------------------------------------------------------------------------- #
@app.get("/api/users")
def get_users() -> dict[str, Any]:
    repo.ensure_default_user()
    return {"users": repo.list_users()}


@app.post("/api/users")
def add_user(body: UserIn) -> dict[str, Any]:
    return {"user": repo.create_user(body.name, role=body.role)}


# --------------------------------------------------------------------------- #
# Chat & briefings
# --------------------------------------------------------------------------- #
@app.post("/api/chat")
def chat(body: ChatIn) -> dict[str, Any]:
    if not body.message.strip():
        raise HTTPException(400, "Empty message.")
    user = _resolve_user(body.user_id)
    return assistant_mod.chat(body.message, speaker=user["name"], user_id=user["id"])


@app.get("/api/briefing/{kind}")
def briefing(kind: str, user_id: Optional[int] = None) -> dict[str, Any]:
    if kind not in ("morning", "evening", "weekly", "monthly"):
        raise HTTPException(404, "Unknown briefing kind.")
    user = _resolve_user(user_id)
    return assistant_mod.narrate_briefing(kind, speaker=user["name"], user_id=user["id"])


# --------------------------------------------------------------------------- #
# Dashboard aggregate
# --------------------------------------------------------------------------- #
@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return {
        "projects": repo.list_projects(include_closed=False),
        "tasks": repo.list_tasks(),
        "events": repo.list_upcoming_events(days=14),
        "commitments": repo.list_commitments("open"),
        "decisions": repo.list_decisions("open"),
        "people": repo.list_people(),
        "standing_orders": repo.list_standing_orders(active_only=True),
        "memory": repo.list_memory(),
        "conflicts": [
            {"a": a["title"], "b": b["title"], "when": b["starts_at"]}
            for a, b in repo.detect_conflicts()
        ],
        "recent_actions": repo.recent_actions(limit=20),
    }


@app.get("/api/snapshot")
def snapshot() -> dict[str, Any]:
    return {"snapshot": operating_snapshot()}


# --------------------------------------------------------------------------- #
# Direct CRUD for the dashboard (thin wrappers over the repository)
# --------------------------------------------------------------------------- #
_CREATE = {
    "projects": repo.create_project,
    "tasks": repo.create_task,
    "commitments": repo.create_commitment,
    "people": repo.create_person,
    "events": repo.create_event,
    "decisions": repo.create_decision,
}
_UPDATE = {
    "projects": repo.update_project,
    "tasks": repo.update_task,
    "commitments": repo.update_commitment,
    "people": repo.update_person,
    "events": repo.update_event,
    "decisions": repo.update_decision,
}
_ID_ARG = {
    "projects": "project_id",
    "tasks": "task_id",
    "commitments": "commitment_id",
    "people": "person_id",
    "events": "event_id",
    "decisions": "decision_id",
}


@app.post("/api/{entity}")
def create_entity(entity: str, body: GenericIn) -> dict[str, Any]:
    fn = _CREATE.get(entity)
    if not fn:
        raise HTTPException(404, f"Unknown entity '{entity}'.")
    data = body.model_dump()
    obj = fn(**data)
    repo.log_action(f"create_{entity}", f"Created {entity[:-1]} via dashboard", payload=data, autonomy_level=3, actor="user")
    return {"item": obj}


@app.patch("/api/{entity}/{item_id}")
def update_entity(entity: str, item_id: int, body: GenericIn) -> dict[str, Any]:
    fn = _UPDATE.get(entity)
    if not fn:
        raise HTTPException(404, f"Unknown entity '{entity}'.")
    data = body.model_dump()
    obj = fn(item_id, **data)
    if not obj:
        raise HTTPException(404, f"No {entity[:-1]} with id {item_id}.")
    repo.log_action(f"update_{entity}", f"Updated {entity[:-1]} #{item_id} via dashboard", payload=data, autonomy_level=3, actor="user")
    return {"item": obj}


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int, confirmed: bool = False) -> dict[str, Any]:
    # Deletion honors the confirmation gate even from the dashboard.
    if not confirmed:
        raise HTTPException(400, "Deletion requires ?confirmed=true (confirmation gate).")
    ok = repo.delete_event(event_id)
    if not ok:
        raise HTTPException(404, f"No event with id {event_id}.")
    repo.log_action("delete_event", f"Deleted event #{event_id} (confirmed)", autonomy_level=4, actor="user")
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Standing orders & memory management
# --------------------------------------------------------------------------- #
@app.post("/api/standing_orders")
def add_standing_order(body: GenericIn) -> dict[str, Any]:
    d = body.model_dump()
    o = repo.create_standing_order(
        title=d.get("title", "Untitled"),
        instruction=d.get("instruction", ""),
        autonomy_level=int(d.get("autonomy_level", 3)),
    )
    return {"item": o}


@app.post("/api/standing_orders/{order_id}/toggle")
def toggle_standing_order(order_id: int, active: bool = True) -> dict[str, Any]:
    o = repo.set_standing_order_active(order_id, active)
    if not o:
        raise HTTPException(404, "No such standing order.")
    return {"item": o}


@app.post("/api/memory")
def add_memory(body: GenericIn) -> dict[str, Any]:
    d = body.model_dump()
    m = repo.add_memory(
        content=d.get("content", ""),
        scope=d.get("scope", "long_term"),
        category=d.get("category", "note"),
    )
    return {"item": m}


# --------------------------------------------------------------------------- #
# Static UI
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(Exception)
def _unhandled(_request, exc: Exception) -> JSONResponse:  # pragma: no cover
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})
