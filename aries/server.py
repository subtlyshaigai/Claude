"""FastAPI application: serves the local web UI and the JSON API that both the
UI and any household device use to talk to Aries. Adds family authentication
and the Google (Gmail + Calendar) integration.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from . import assistant as assistant_mod
from . import auth
from . import repository as repo
from . import sync as sync_mod
from .briefings import operating_snapshot
from .config import settings
from .database import get_connection
from .integrations import google

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_connection()  # create schema + migrate
    repo.ensure_default_user()
    repo.prune_sessions()
    pruned = repo.prune_old_messages(settings.chatlog_retention_days)
    if pruned:
        repo.log_action("maintenance", f"Pruned {pruned} chat log rows past retention", autonomy_level=0)
    yield


app = FastAPI(title="Aries — Executive Chief of Staff", version=__version__, lifespan=lifespan)

CurrentUser = Depends(auth.current_user)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class ChatIn(BaseModel):
    message: str


class UserIn(BaseModel):
    name: str
    role: str = "family"
    password: Optional[str] = None


class SetupIn(BaseModel):
    name: str = "Principal"
    password: str


class LoginIn(BaseModel):
    name: str
    password: str


class GenericIn(BaseModel):
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
        "require_auth": settings.require_auth,
        "google": google.connection_status(),
    }


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
@app.get("/api/auth/state")
def auth_state(user: Optional[dict] = Depends(auth.optional_user)) -> dict[str, Any]:
    return {
        "require_auth": settings.require_auth,
        "setup_needed": settings.require_auth and not repo.any_password_set(),
        "authenticated": user is not None,
        "user": {"id": user["id"], "name": user["name"], "role": user["role"]} if user else None,
    }


@app.post("/api/auth/setup")
def auth_setup(body: SetupIn) -> dict[str, Any]:
    """First-run: set the Principal's password. Refused once any password exists."""
    if repo.any_password_set():
        raise HTTPException(400, "Setup already completed. Use login.")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters.")
    principal = repo.ensure_default_user()
    if body.name.strip() and body.name.strip() != principal["name"]:
        # Rename the seeded principal to the chosen name if free.
        if not repo.get_user_by_name(body.name):
            get_connection().execute("UPDATE users SET name=? WHERE id=?", (body.name.strip(), principal["id"]))
            get_connection().commit()
            principal = repo.get_user(principal["id"])
    h, s = auth.hash_password(body.password)
    repo.set_user_password(principal["id"], h, s)
    return {"ok": True, "user": {"id": principal["id"], "name": principal["name"]}}


@app.post("/api/auth/login")
def auth_login(body: LoginIn, response: Response) -> dict[str, Any]:
    user = repo.get_user_by_name(body.name)
    if not user or not auth.verify_password(body.password, user["password_hash"], user["password_salt"]):
        raise HTTPException(401, "Invalid name or password.")
    token = auth.start_session(user["id"])
    response.set_cookie(
        auth.COOKIE_NAME, token, httponly=True, samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
    )
    return {"ok": True, "user": {"id": user["id"], "name": user["name"], "role": user["role"]}}


@app.post("/api/auth/logout")
def auth_logout(response: Response, aries_session: str | None = Cookie(default=None)) -> dict[str, Any]:
    auth.end_session(aries_session)
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Users / family
# --------------------------------------------------------------------------- #
@app.get("/api/users")
def get_users(_user: dict = CurrentUser) -> dict[str, Any]:
    users = repo.list_users()
    # Never leak password material.
    for u in users:
        u.pop("password_hash", None)
        u.pop("password_salt", None)
    return {"users": users}


@app.post("/api/users")
def add_user(body: UserIn, user: dict = CurrentUser) -> dict[str, Any]:
    if user["role"] != "principal" and settings.require_auth:
        raise HTTPException(403, "Only the Principal may add members.")
    if repo.get_user_by_name(body.name):
        raise HTTPException(400, "A member with that name already exists.")
    new_user = repo.create_user(body.name, role=body.role)
    if body.password:
        if len(body.password) < 6:
            raise HTTPException(400, "Password must be at least 6 characters.")
        h, s = auth.hash_password(body.password)
        repo.set_user_password(new_user["id"], h, s)
    new_user.pop("password_hash", None)
    new_user.pop("password_salt", None)
    return {"user": new_user}


# --------------------------------------------------------------------------- #
# Chat & briefings
# --------------------------------------------------------------------------- #
@app.post("/api/chat")
def chat(body: ChatIn, user: dict = CurrentUser) -> dict[str, Any]:
    if not body.message.strip():
        raise HTTPException(400, "Empty message.")
    return assistant_mod.chat(body.message, speaker=user["name"], user_id=user["id"])


@app.get("/api/briefing/{kind}")
def briefing(kind: str, user: dict = CurrentUser) -> dict[str, Any]:
    if kind not in ("morning", "evening", "weekly", "monthly"):
        raise HTTPException(404, "Unknown briefing kind.")
    return assistant_mod.narrate_briefing(kind, speaker=user["name"], user_id=user["id"])


# --------------------------------------------------------------------------- #
# Dashboard aggregate
# --------------------------------------------------------------------------- #
@app.get("/api/dashboard")
def dashboard(_user: dict = CurrentUser) -> dict[str, Any]:
    return {
        "projects": repo.list_projects(include_closed=False),
        "tasks": repo.list_tasks(),
        "events": repo.list_upcoming_events(days=14),
        "commitments": repo.list_commitments("open"),
        "decisions": repo.list_decisions("open"),
        "people": repo.list_people(),
        "standing_orders": repo.list_standing_orders(active_only=True),
        "memory": repo.list_memory(),
        "emails": repo.list_emails(limit=40),
        "conflicts": [
            {"a": a["title"], "b": b["title"], "when": b["starts_at"]}
            for a, b in repo.detect_conflicts()
        ],
        "recent_actions": repo.recent_actions(limit=20),
    }


@app.get("/api/snapshot")
def snapshot(_user: dict = CurrentUser) -> dict[str, Any]:
    return {"snapshot": operating_snapshot()}


# --------------------------------------------------------------------------- #
# Google integration
# --------------------------------------------------------------------------- #
@app.get("/api/integrations/google/status")
def google_status(_user: dict = CurrentUser) -> dict[str, Any]:
    return google.connection_status()


@app.get("/api/integrations/google/connect")
def google_connect(user: dict = CurrentUser) -> dict[str, Any]:
    if user["role"] != "principal" and settings.require_auth:
        raise HTTPException(403, "Only the Principal may connect a Google account.")
    try:
        return {"auth_url": google.authorization_url()}
    except google.GoogleUnavailable as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/integrations/google/callback")
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(url=f"/?google_error={error}")
    if not code:
        return RedirectResponse(url="/?google_error=missing_code")
    try:
        google.exchange_code(code, state=state)
    except google.GoogleUnavailable as exc:
        return RedirectResponse(url=f"/?google_error={exc}")
    return RedirectResponse(url="/?google_connected=1")


@app.post("/api/integrations/google/disconnect")
def google_disconnect(user: dict = CurrentUser) -> dict[str, Any]:
    if user["role"] != "principal" and settings.require_auth:
        raise HTTPException(403, "Only the Principal may disconnect the Google account.")
    google.disconnect()
    return {"ok": True}


@app.post("/api/integrations/google/sync")
def google_sync(_user: dict = CurrentUser) -> dict[str, Any]:
    if not google.is_connected():
        raise HTTPException(400, "No Google account connected.")
    try:
        cal = sync_mod.sync_calendar()
        inbox = sync_mod.sync_inbox()
    except google.GoogleUnavailable as exc:
        raise HTTPException(400, str(exc))
    return {"calendar": cal, "inbox": inbox}


@app.get("/api/emails")
def emails(unread_only: bool = False, _user: dict = CurrentUser) -> dict[str, Any]:
    return {"emails": repo.list_emails(unread_only=unread_only)}


# --------------------------------------------------------------------------- #
# Standing orders & memory management
# --------------------------------------------------------------------------- #
@app.post("/api/standing_orders")
def add_standing_order(body: GenericIn, _user: dict = CurrentUser) -> dict[str, Any]:
    d = body.model_dump()
    o = repo.create_standing_order(
        title=d.get("title", "Untitled"),
        instruction=d.get("instruction", ""),
        autonomy_level=int(d.get("autonomy_level", 3)),
    )
    return {"item": o}


@app.post("/api/standing_orders/{order_id}/toggle")
def toggle_standing_order(order_id: int, active: bool = True, _user: dict = CurrentUser) -> dict[str, Any]:
    o = repo.set_standing_order_active(order_id, active)
    if not o:
        raise HTTPException(404, "No such standing order.")
    return {"item": o}


@app.post("/api/memory")
def add_memory(body: GenericIn, _user: dict = CurrentUser) -> dict[str, Any]:
    d = body.model_dump()
    m = repo.add_memory(
        content=d.get("content", ""),
        scope=d.get("scope", "long_term"),
        category=d.get("category", "note"),
    )
    return {"item": m}


# --------------------------------------------------------------------------- #
# Generic CRUD for the dashboard.
# Declared AFTER the specific POST routes above so the {entity} catch-all does
# not shadow /api/standing_orders, /api/memory, etc.
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


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int, confirmed: bool = False, _user: dict = CurrentUser) -> dict[str, Any]:
    if not confirmed:
        raise HTTPException(400, "Deletion requires ?confirmed=true (confirmation gate).")
    ok = repo.delete_event(event_id)
    if not ok:
        raise HTTPException(404, f"No event with id {event_id}.")
    repo.log_action("delete_event", f"Deleted event #{event_id} (confirmed)", autonomy_level=4, actor="user")
    return {"deleted": True}


@app.post("/api/{entity}")
def create_entity(entity: str, body: GenericIn, _user: dict = CurrentUser) -> dict[str, Any]:
    fn = _CREATE.get(entity)
    if not fn:
        raise HTTPException(404, f"Unknown entity '{entity}'.")
    data = body.model_dump()
    obj = fn(**data)
    repo.log_action(f"create_{entity}", f"Created {entity[:-1]} via dashboard", payload=data, autonomy_level=3, actor="user")
    return {"item": obj}


@app.patch("/api/{entity}/{item_id}")
def update_entity(entity: str, item_id: int, body: GenericIn, _user: dict = CurrentUser) -> dict[str, Any]:
    fn = _UPDATE.get(entity)
    if not fn:
        raise HTTPException(404, f"Unknown entity '{entity}'.")
    data = body.model_dump()
    obj = fn(item_id, **data)
    if not obj:
        raise HTTPException(404, f"No {entity[:-1]} with id {item_id}.")
    repo.log_action(f"update_{entity}", f"Updated {entity[:-1]} #{item_id} via dashboard", payload=data, autonomy_level=3, actor="user")
    return {"item": obj}


# --------------------------------------------------------------------------- #
# Static UI
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(Exception)
def _unhandled(_request, exc: Exception) -> JSONResponse:  # pragma: no cover
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})
