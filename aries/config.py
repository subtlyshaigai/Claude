"""Configuration loading for Aries.

All runtime configuration comes from environment variables (optionally supplied
through a local ``.env`` file). Nothing here reaches the network on import.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


# Repo root is the parent of the ``aries`` package directory.
ROOT_DIR = Path(__file__).resolve().parent.parent


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else (ROOT_DIR / p)


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    model: str
    max_tokens: int
    host: str
    port: int
    db_path: Path
    integrity_phrase: str
    chatlog_retention_days: int

    @property
    def llm_enabled(self) -> bool:
        """True when an API key is present. When False, Aries runs in a limited
        offline mode: local data tools still work, but conversational reasoning
        is unavailable."""
        return bool(self.anthropic_api_key.strip())


def load_settings() -> Settings:
    # Load .env from repo root if present. Real environment variables win.
    load_dotenv(ROOT_DIR / ".env")

    def _int(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        try:
            return int(raw) if raw else default
        except ValueError:
            return default

    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        model=os.getenv("ARIES_MODEL", "claude-sonnet-4-5").strip() or "claude-sonnet-4-5",
        max_tokens=_int("ARIES_MAX_TOKENS", 4096),
        host=os.getenv("ARIES_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_int("ARIES_PORT", 8787),
        db_path=_resolve(os.getenv("ARIES_DB_PATH", "data/aries.db").strip() or "data/aries.db"),
        integrity_phrase=os.getenv("ARIES_INTEGRITY_PHRASE", "The stars hold steady.").strip()
        or "The stars hold steady.",
        chatlog_retention_days=_int("ARIES_CHATLOG_RETENTION_DAYS", 7),
    )


# Module-level singleton used across the app.
settings = load_settings()
