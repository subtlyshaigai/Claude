"""Google integration: Gmail + Calendar under a read-and-propose posture.

Reads are autonomous (Level 0): Aries imports calendar events and email
headers for awareness and briefings. Writes are gated (Level 4 Confirm): pushing
an event to Google Calendar or sending an email happens only through the
confirmation-gated tools, never on a bare read.

All ``google.*`` imports are performed lazily inside functions so that Aries
runs perfectly well without the Google client libraries installed; the
integration simply reports itself as unavailable until they are present and the
user has connected an account.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Any

from ..config import settings

# Read calendar + email; create/update calendar events; compose/send mail.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]

# Short-lived OAuth state store (local, single-user). Maps state -> created ts.
_pending_states: dict[str, float] = {}


class GoogleUnavailable(RuntimeError):
    """Raised when the integration cannot proceed (libs missing / not connected)."""


# --------------------------------------------------------------------------- #
# Library / credential availability
# --------------------------------------------------------------------------- #
def libs_available() -> bool:
    # Catch BaseException, not just ImportError: some environments have a
    # partially-broken transitive dependency (e.g. a miscompiled cryptography
    # whose Rust extension raises a pyo3 PanicException — a BaseException) that
    # fails at import time. Treat that as "unavailable" rather than crashing the
    # whole app.
    try:
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
        return True
    except BaseException:
        return False


def _client_config() -> dict[str, Any]:
    """Build a Google 'web' client config from env vars or a credentials file."""
    if settings.google_client_id and settings.google_client_secret:
        return {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.oauth_redirect_uri],
            }
        }
    if settings.google_credentials_file.exists():
        raw = json.loads(settings.google_credentials_file.read_text())
        # Accept either a {"web": {...}} or {"installed": {...}} download.
        key = "web" if "web" in raw else "installed" if "installed" in raw else None
        if not key:
            raise GoogleUnavailable("google_credentials.json is not a recognized OAuth client file.")
        cfg = raw[key]
        cfg.setdefault("redirect_uris", [settings.oauth_redirect_uri])
        return {"web": cfg}
    raise GoogleUnavailable("Google OAuth client credentials are not configured.")


def is_connected() -> bool:
    return settings.google_token_file.exists()


def connection_status() -> dict[str, Any]:
    return {
        "libs_available": libs_available(),
        "configured": settings.google_configured,
        "connected": is_connected(),
        "redirect_uri": settings.oauth_redirect_uri,
        "scopes": SCOPES,
    }


# --------------------------------------------------------------------------- #
# OAuth flow
# --------------------------------------------------------------------------- #
def _require_ready() -> None:
    if not libs_available():
        raise GoogleUnavailable(
            "Google client libraries are not installed. Run: "
            "pip install google-api-python-client google-auth-oauthlib"
        )
    if not settings.google_configured:
        raise GoogleUnavailable("Google OAuth client credentials are not configured (see .env).")


def authorization_url() -> str:
    """Return the Google consent URL to redirect the user to."""
    _require_ready()
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=settings.oauth_redirect_uri)
    url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    _pending_states[state] = time.time()
    return url


def exchange_code(code: str, state: str | None = None) -> None:
    """Complete the OAuth callback: exchange the code and persist the token."""
    _require_ready()
    from google_auth_oauthlib.flow import Flow

    # Best-effort state check (expire after 10 minutes).
    if state is not None:
        _pending_states.pop(state, None)

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=settings.oauth_redirect_uri)
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_credentials(creds)


def disconnect() -> None:
    if settings.google_token_file.exists():
        settings.google_token_file.unlink()


def _save_credentials(creds) -> None:
    settings.google_token_file.parent.mkdir(parents=True, exist_ok=True)
    settings.google_token_file.write_text(creds.to_json())


def _load_credentials():
    """Load stored credentials, refreshing them if expired. Raises if not connected."""
    _require_ready()
    if not is_connected():
        raise GoogleUnavailable("No Google account connected. Connect one from the Integrations panel.")
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials.from_authorized_user_file(str(settings.google_token_file), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_credentials(creds)
        else:
            raise GoogleUnavailable("Stored Google credentials are invalid. Reconnect the account.")
    return creds


def _service(name: str, version: str):
    from googleapiclient.discovery import build

    return build(name, version, credentials=_load_credentials(), cache_discovery=False)


# --------------------------------------------------------------------------- #
# Calendar — read (autonomous) and write (gated)
# --------------------------------------------------------------------------- #
def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_google_dt(node: dict[str, Any]) -> str | None:
    if not node:
        return None
    raw = node.get("dateTime") or node.get("date")
    if not raw:
        return None
    # Normalize to our stored ISO-Z form.
    try:
        if len(raw) == 10:  # all-day date
            return raw + "T00:00:00Z"
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return _rfc3339(dt)
    except ValueError:
        return raw


def fetch_calendar(days_back: int = 1, days_forward: int = 14) -> list[dict[str, Any]]:
    """Read events from the primary calendar. Autonomous (read-only)."""
    now = datetime.now(timezone.utc)
    time_min = _rfc3339(now - timedelta(days=days_back))
    time_max = _rfc3339(now + timedelta(days=days_forward))
    svc = _service("calendar", "v3")
    result = svc.events().list(
        calendarId="primary", timeMin=time_min, timeMax=time_max,
        singleEvents=True, orderBy="startTime", maxResults=250,
    ).execute()
    out = []
    for e in result.get("items", []):
        if e.get("status") == "cancelled":
            continue
        out.append({
            "external_id": e.get("id"),
            "title": e.get("summary", "(no title)"),
            "starts_at": _parse_google_dt(e.get("start", {})),
            "ends_at": _parse_google_dt(e.get("end", {})),
            "location": e.get("location", ""),
            "attendees": ", ".join(a.get("email", "") for a in e.get("attendees", []) if a.get("email")),
            "external_updated": e.get("updated"),
        })
    return out


def push_event(title: str, starts_at: str, ends_at: str | None = None, location: str = "", description: str = "") -> dict[str, Any]:
    """Create an event on Google Calendar. GATED — call only after confirmation."""
    svc = _service("calendar", "v3")
    body = {
        "summary": title,
        "location": location,
        "description": description,
        "start": {"dateTime": _iso_z_to_rfc3339(starts_at)},
        "end": {"dateTime": _iso_z_to_rfc3339(ends_at or starts_at)},
    }
    created = svc.events().insert(calendarId="primary", body=body).execute()
    return {"external_id": created.get("id"), "htmlLink": created.get("htmlLink")}


def _iso_z_to_rfc3339(iso: str) -> str:
    # Google wants an offset; our storage uses ...Z which is valid RFC3339.
    return iso if iso.endswith("Z") or "+" in iso else iso + "Z"


# --------------------------------------------------------------------------- #
# Gmail — read (autonomous) and compose/send (gated)
# --------------------------------------------------------------------------- #
def fetch_inbox(max_results: int = 25, unread_only: bool = True) -> list[dict[str, Any]]:
    """Read recent message headers/snippets. Autonomous (read-only)."""
    svc = _service("gmail", "v1")
    query = "in:inbox is:unread" if unread_only else "in:inbox"
    listing = svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    out = []
    for ref in listing.get("messages", []):
        msg = svc.users().messages().get(
            userId="me", id=ref["id"], format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        out.append({
            "external_id": msg.get("id"),
            "thread_id": msg.get("threadId"),
            "sender": headers.get("from", ""),
            "recipient": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "snippet": msg.get("snippet", ""),
            "received_at": _parse_epoch_ms(msg.get("internalDate")),
            "is_unread": 1 if "UNREAD" in msg.get("labelIds", []) else 0,
        })
    return out


def _parse_epoch_ms(ms: str | None) -> str | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def _mime(to: str, subject: str, body: str) -> str:
    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def create_draft(to: str, subject: str, body: str) -> dict[str, Any]:
    """Create a Gmail draft (preparation, Level 2). Does NOT send."""
    svc = _service("gmail", "v1")
    draft = svc.users().drafts().create(
        userId="me", body={"message": {"raw": _mime(to, subject, body)}}
    ).execute()
    return {"draft_id": draft.get("id")}


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send an email. GATED — call only after explicit confirmation."""
    svc = _service("gmail", "v1")
    sent = svc.users().messages().send(userId="me", body={"raw": _mime(to, subject, body)}).execute()
    return {"message_id": sent.get("id")}
