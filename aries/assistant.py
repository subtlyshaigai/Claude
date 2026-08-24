"""The reasoning core: runs Aries' conversation loop against Claude with tool
use, enforces the operating law through the system prompt, and degrades
gracefully when the model is unreachable (offline mode per the spec).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import repository as repo
from . import tools as tools_mod
from .briefings import build_digest, operating_snapshot
from .config import settings
from .persona import SYSTEM_PROMPT, build_runtime_context

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None  # type: ignore


_MAX_TOOL_ITERATIONS = 8


class AssistantUnavailable(RuntimeError):
    """Raised when reasoning cannot proceed (no key / SDK / connectivity)."""


def _client():
    if anthropic is None:
        raise AssistantUnavailable("The 'anthropic' package is not installed. Run: pip install -r requirements.txt")
    if not settings.llm_enabled:
        raise AssistantUnavailable("No ANTHROPIC_API_KEY configured. Add it to your .env file.")
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _greeting(speaker: str) -> str:
    hour = datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return f"Good {part}, {speaker}."


def _history_to_messages(user_id: int, limit: int = 16) -> list[dict[str, Any]]:
    """Convert stored plain-text history into the Anthropic messages format.
    Only user/assistant turns are included; tool round-trips are not persisted."""
    msgs: list[dict[str, Any]] = []
    for m in repo.recent_messages(limit=limit, user_id=user_id):
        if m["role"] in ("user", "assistant"):
            msgs.append({"role": m["role"], "content": m["content"]})
    return msgs


def chat(user_message: str, *, speaker: str, user_id: int) -> dict[str, Any]:
    """Run one conversational turn. Returns a dict with the reply text and a
    list of tool actions taken (for UI transparency)."""
    repo.log_message("user", user_message, user_id=user_id)

    try:
        client = _client()
    except AssistantUnavailable as exc:
        # Offline / unconfigured: still useful for local data via the API, but
        # no natural-language reasoning. Surface the integrity phrase so the
        # user can trust this is a genuine Aries notice.
        reply = (
            f"{settings.integrity_phrase}\n\n"
            f"Reasoning is unavailable — {exc} Aries is running in offline mode: "
            f"your local data, dashboard, and briefings still work, but I cannot "
            f"hold a conversation until the model is reachable."
        )
        repo.log_message("assistant", reply, user_id=user_id)
        return {"reply": reply, "actions": [], "offline": True}

    history = _history_to_messages(user_id)
    messages: list[dict[str, Any]] = [*history, {"role": "user", "content": user_message}]

    snapshot = operating_snapshot() + "\n\n" + _integration_line()
    runtime_context = build_runtime_context(speaker=speaker, snapshot=snapshot)
    system_blocks = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "text", "text": runtime_context},
    ]

    actions: list[dict[str, Any]] = []

    for _ in range(_MAX_TOOL_ITERATIONS):
        try:
            response = client.messages.create(
                model=settings.model,
                max_tokens=settings.max_tokens,
                system=system_blocks,
                tools=tools_mod.TOOL_SCHEMAS,
                messages=messages,
            )
        except Exception as exc:  # network / API failure -> offline behavior
            reply = (
                f"{settings.integrity_phrase}\n\n"
                f"I could not reach the reasoning model ({type(exc).__name__}). "
                f"Switching to offline mode; local tools and data remain available. "
                f"I will hold this request until connectivity returns."
            )
            repo.log_message("assistant", reply, user_id=user_id)
            return {"reply": reply, "actions": actions, "offline": True}

        if response.stop_reason != "tool_use":
            reply = _text_from(response)
            repo.log_message("assistant", reply, user_id=user_id)
            return {"reply": reply, "actions": actions, "offline": False}

        # Execute each requested tool and feed results back.
        assistant_content = [block.model_dump() for block in response.content]
        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = tools_mod.dispatch(block.name, dict(block.input))
            actions.append({"tool": block.name, "input": dict(block.input), "result": result})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": _stringify(result),
            })
        messages.append({"role": "user", "content": tool_results})

    reply = "I reached my tool-use limit for this turn. Here is where I stopped — please advise how to proceed."
    repo.log_message("assistant", reply, user_id=user_id)
    return {"reply": reply, "actions": actions, "offline": False}


def narrate_briefing(kind: str, *, speaker: str, user_id: int) -> dict[str, Any]:
    """Produce a briefing. The deterministic digest is authoritative; if the
    model is available, Aries renders it in voice, otherwise the raw digest is
    returned so briefings work offline."""
    digest = build_digest(kind)
    try:
        client = _client()
    except AssistantUnavailable:
        return {"reply": digest, "actions": [], "offline": True}

    prompt = (
        f"Render the following {kind} briefing for {speaker} in your voice: formal, "
        f"concise, decision-oriented (Issue → Context → Impact → Recommendation → "
        f"Required Decision where relevant). Do not invent data beyond the digest. "
        f"Lead with the single most important thing.\n\n{digest}"
    )
    try:
        response = client.messages.create(
            model=settings.model,
            max_tokens=settings.max_tokens,
            system=[{"type": "text", "text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": prompt}],
        )
        reply = _text_from(response)
    except Exception:
        return {"reply": digest, "actions": [], "offline": True}

    repo.log_message("assistant", f"[{kind} briefing]\n{reply}", user_id=user_id)
    return {"reply": reply, "actions": [], "offline": False, "digest": digest}


def _integration_line() -> str:
    """One line telling Aries whether the Google integration is usable."""
    try:
        from .integrations import google
        st = google.connection_status()
    except Exception:
        return "INTEGRATIONS: Google — status unknown."
    if st["connected"]:
        return ("INTEGRATIONS: Google is CONNECTED. You may sync_google_calendar and "
                "sync_google_inbox freely (read-only). Sending email and pushing calendar "
                "events are gated — confirm with the user, then pass confirmed=true.")
    if st["configured"]:
        return "INTEGRATIONS: Google is configured but NOT connected. Ask the Principal to connect it in the Integrations panel."
    return "INTEGRATIONS: Google is not configured. Calendar/email tools are unavailable until set up."


def _text_from(response) -> str:
    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip() or "(no response)"


def _stringify(result: dict[str, Any]) -> str:
    import json
    return json.dumps(result, ensure_ascii=False, default=str)
