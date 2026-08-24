#!/usr/bin/env python3
"""Launch Aries locally.

    python run.py

Then open the printed URL in your browser. Configuration comes from your .env
file (copy .env.example to .env first).
"""

from __future__ import annotations

import sys

import uvicorn

from aries.config import settings


def main() -> int:
    print("=" * 60)
    print("  Aries — Executive Chief of Staff (Constellation01)")
    print("=" * 60)
    print(f"  URL          : http://{settings.host}:{settings.port}")
    print(f"  Model        : {settings.model if settings.llm_enabled else '(offline — no API key)'}")
    print(f"  Database     : {settings.db_path}")
    print(f"  Chat retention: {settings.chatlog_retention_days} days")
    if not settings.llm_enabled:
        print()
        print("  NOTE: ANTHROPIC_API_KEY is not set. Aries will run in offline")
        print("  mode (dashboard, data, and briefings work; conversation does not).")
        print("  Add your key to .env to enable full reasoning.")
    print("=" * 60)

    uvicorn.run(
        "aries.server:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
