"""Shared test configuration. Runs before any test module is imported, so the
environment is set before ``aries.config`` reads it.

Defaults: a throwaway database, no Anthropic key (offline path), and auth off so
data-layer tests don't each need to log in. The auth tests flip auth on
explicitly via the frozen-settings escape hatch.
"""

import os
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aries-tests-")
os.environ["ARIES_DB_PATH"] = str(Path(_TMP) / "test.db")
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["ARIES_REQUIRE_AUTH"] = "false"
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""
os.environ["GOOGLE_CREDENTIALS_FILE"] = str(Path(_TMP) / "nope_credentials.json")
os.environ["GOOGLE_TOKEN_FILE"] = str(Path(_TMP) / "token.json")
