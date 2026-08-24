"""Authentication tests. Auth is off by default (conftest), so these flip it on
for the duration of each test via the frozen-settings escape hatch and restore
it afterward.
"""

import pytest
from fastapi.testclient import TestClient

from aries import auth
from aries.config import settings
from aries.server import app


@pytest.fixture
def auth_on():
    object.__setattr__(settings, "require_auth", True)
    try:
        yield
    finally:
        object.__setattr__(settings, "require_auth", False)


def test_password_hash_roundtrip():
    h, s = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", h, s)
    assert not auth.verify_password("wrong", h, s)


def test_setup_login_and_protection(auth_on):
    with TestClient(app) as client:
        state = client.get("/api/auth/state").json()
        assert state["require_auth"] is True

        # Protected endpoint refuses without a session.
        assert client.get("/api/dashboard").status_code == 401

        # If setup is still needed, complete it; otherwise a prior test set it.
        if state["setup_needed"]:
            r = client.post("/api/auth/setup", json={"name": "TestPrincipal", "password": "familypass"})
            assert r.status_code == 200
            login_name = "TestPrincipal"
        else:
            login_name = None

        if login_name:
            bad = client.post("/api/auth/login", json={"name": login_name, "password": "nope"})
            assert bad.status_code == 401
            ok = client.post("/api/auth/login", json={"name": login_name, "password": "familypass"})
            assert ok.status_code == 200
            # Now the session cookie grants access.
            assert client.get("/api/dashboard").status_code == 200
            # Logout clears it.
            client.post("/api/auth/logout")
            assert client.get("/api/dashboard").status_code == 401
