"""Tests for Phase B: users.json, password hashing, signed session cookies,
first-run setup, and per-user character authorization.

`app_module` / `client` fixtures and `create_and_login()` live in
conftest.py (shared with test_characters.py).
"""

from __future__ import annotations

import time

from conftest import create_and_login, make_character as _make_character


# ===================== Password hashing =====================

def test_hash_password_roundtrip(app_module):
    stored = app_module._hash_password("correct horse battery staple")
    assert stored.startswith("scrypt$")
    assert app_module._verify_password("correct horse battery staple", stored)
    assert not app_module._verify_password("wrong password", stored)


def test_hash_password_uses_distinct_salts(app_module):
    a = app_module._hash_password("same-password")
    b = app_module._hash_password("same-password")
    assert a != b  # different random salt each time
    assert app_module._verify_password("same-password", a)
    assert app_module._verify_password("same-password", b)


def test_password_hash_never_stored_in_plaintext(app_module, tmp_path):
    app_module.create_user("alice", "Alice", "hunter2")
    doc = app_module._load_users_doc()
    stored = doc["users"][0]["password_hash"]
    assert "hunter2" not in stored
    assert stored.startswith("scrypt$")


# ===================== Session cookie signing =====================

def test_session_token_roundtrip(app_module):
    token = app_module.make_session_token("alice")
    assert app_module.verify_session_token(token) == "alice"


def test_session_token_tamper_detected(app_module):
    token = app_module.make_session_token("alice")
    user_id, expires, sig = token.split(":")
    tampered = f"bob:{expires}:{sig}"
    assert app_module.verify_session_token(tampered) is None


def test_session_token_expired_rejected(app_module, monkeypatch):
    token = app_module.make_session_token("alice")
    # Jump time forward past SESSION_MAX_AGE.
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + app_module.SESSION_MAX_AGE + 10)
    assert app_module.verify_session_token(token) is None


def test_session_secret_file_is_created_0600(app_module, tmp_path):
    app_module._get_session_secret()
    secret_file = tmp_path / ".session_secret"
    assert secret_file.exists()
    mode = secret_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_session_secret_is_stable_across_calls(app_module):
    a = app_module._get_session_secret()
    b = app_module._get_session_secret()
    assert a == b


# ===================== users.json =====================

def test_create_user_persists_to_users_json(app_module, tmp_path):
    app_module.create_user("alice", "Alice", "s3cret", color="#f58e7d", characters="all")
    users_file = tmp_path / "users.json"
    assert users_file.exists()
    assert app_module.find_user("alice") is not None
    assert app_module.find_user("alice")["name"] == "Alice"


def test_create_user_rejects_duplicate_id(app_module):
    app_module.create_user("alice", "Alice", "s3cret")
    try:
        app_module.create_user("alice", "Someone Else", "other-pw")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_create_user_rejects_invalid_id(app_module):
    try:
        app_module.create_user("has space", "Bad", "s3cret")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_users_exist_reflects_users_json(app_module):
    assert app_module.users_exist() is False
    app_module.create_user("alice", "Alice", "s3cret")
    assert app_module.users_exist() is True


def test_list_users_never_includes_password_hash(app_module):
    app_module.create_user("alice", "Alice", "s3cret")
    for u in app_module.list_users():
        assert "password_hash" not in u


def test_authenticate_success_and_failure(app_module):
    app_module.create_user("alice", "Alice", "s3cret")
    assert app_module.authenticate("alice", "s3cret") is not None
    assert app_module.authenticate("alice", "wrong") is None
    assert app_module.authenticate("nobody", "s3cret") is None


# ===================== Setup page branching (first-run flow) =====================

class TestSetupFlow:
    def test_setup_page_shown_when_no_users(self, app_module, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/setup"

    def test_setup_api_blocked_once_users_exist(self, app_module, client):
        create_and_login(client)
        resp = client.post(
            "/api/setup",
            json={"id": "bob", "name": "Bob", "password": "pw", "color": "#000"},
        )
        assert resp.status_code == 409

    def test_setup_page_redirects_to_login_once_users_exist(self, app_module, client):
        create_and_login(client)
        resp = client.get("/setup", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/login"

    def test_first_user_gets_all_characters(self, app_module, client):
        body = create_and_login(client)
        user = app_module.find_user(body["user"]["id"])
        assert user["characters"] == "all"


# ===================== Login flow =====================

class TestLogin:
    def test_login_sets_signed_httponly_cookie(self, app_module, client):
        app_module.create_user("alice", "Alice", "s3cret")
        resp = client.post("/api/auth/login", json={"id": "alice", "password": "s3cret"})
        assert resp.status_code == 200
        cookie = resp.cookies.get(app_module.SESSION_COOKIE_NAME)
        assert cookie is not None
        assert app_module.verify_session_token(cookie) == "alice"
        set_cookie_header = resp.headers.get("set-cookie", "")
        assert "httponly" in set_cookie_header.lower()

    def test_login_wrong_password_rejected(self, app_module, client):
        app_module.create_user("alice", "Alice", "s3cret")
        resp = client.post("/api/auth/login", json={"id": "alice", "password": "wrong"})
        assert resp.status_code == 401

    def test_logout_clears_cookie(self, app_module, client):
        create_and_login(client)
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        # A subsequent authenticated call should now fail.
        resp2 = client.get("/api/me")
        assert resp2.status_code == 401

    def test_unauthenticated_api_call_is_401(self, app_module, client):
        create_and_login(client)  # users.json now exists, but this client is fresh
        from fastapi.testclient import TestClient
        anon = TestClient(app_module.app)
        resp = anon.get("/api/characters")
        assert resp.status_code == 401

    def test_unauthenticated_page_redirects_to_login(self, app_module, client):
        create_and_login(client)
        from fastapi.testclient import TestClient
        anon = TestClient(app_module.app)
        resp = anon.get("/", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/login"


# ===================== Per-user character authorization =====================

class TestCharacterAuthorization:
    def test_user_with_all_sees_every_character(self, app_module, tmp_path, client):
        _make_character(tmp_path, "alpha")
        _make_character(tmp_path, "beta")
        create_and_login(client, user_id="alice")

        resp = client.get("/api/characters")
        ids = {c["id"] for c in resp.json()}
        assert ids == {"alpha", "beta"}

    def test_user_with_restricted_list_only_sees_allowed(self, app_module, tmp_path, client):
        _make_character(tmp_path, "alpha")
        _make_character(tmp_path, "beta")
        app_module.create_user("bob", "Bob", "pw", characters=["alpha"])
        client.post("/api/auth/login", json={"id": "bob", "password": "pw"})

        resp = client.get("/api/characters")
        ids = {c["id"] for c in resp.json()}
        assert ids == {"alpha"}

    def test_restricted_user_gets_404_for_disallowed_character(self, app_module, tmp_path, client):
        _make_character(tmp_path, "alpha")
        _make_character(tmp_path, "beta")
        app_module.create_user("bob", "Bob", "pw", characters=["alpha"])
        client.post("/api/auth/login", json={"id": "bob", "password": "pw"})

        assert client.get("/api/alpha/chat/history").status_code == 200
        assert client.get("/api/beta/chat/history").status_code == 404

    def test_allowed_user_gets_200_for_their_character(self, app_module, tmp_path, client):
        _make_character(tmp_path, "alpha")
        app_module.create_user("bob", "Bob", "pw", characters=["alpha"])
        client.post("/api/auth/login", json={"id": "bob", "password": "pw"})

        assert client.get("/api/alpha/chat/history").status_code == 200


# ===================== Chat history split per (character, user) =====================

def test_chat_history_split_by_user_not_just_character(app_module, tmp_path, client):
    _make_character(tmp_path, "alpha")
    app_module.create_user("alice", "Alice", "pw-a")
    app_module.create_user("bob", "Bob", "pw-b", characters="all")

    app_module._append_chat("alpha", "alice", "alice", "hi from alice")
    app_module._append_chat("alpha", "bob", "bob", "hi from bob")

    alice_file = tmp_path / "characters" / "alpha" / "chat_histories" / "alice.json"
    bob_file = tmp_path / "characters" / "alpha" / "chat_histories" / "bob.json"
    assert alice_file.exists()
    assert bob_file.exists()
    assert [e["text"] for e in app_module._load_chat_log("alpha", "alice")] == ["hi from alice"]
    assert [e["text"] for e in app_module._load_chat_log("alpha", "bob")] == ["hi from bob"]


def test_chat_history_isolated_via_api_across_two_logged_in_users(app_module, tmp_path):
    from fastapi.testclient import TestClient

    _make_character(tmp_path, "alpha")

    alice_client = TestClient(app_module.app)
    create_and_login(alice_client, user_id="alice", name="Alice", password="pw-a")

    bob_client = TestClient(app_module.app)
    app_module.create_user("bob", "Bob", "pw-b", characters="all")
    bob_client.post("/api/auth/login", json={"id": "bob", "password": "pw-b"})

    app_module._append_chat("alpha", "alice", "alice", "alice's message")
    app_module._append_chat("alpha", "bob", "bob", "bob's message")

    alice_history = alice_client.get("/api/alpha/chat/history").json()
    bob_history = bob_client.get("/api/alpha/chat/history").json()
    assert [e["text"] for e in alice_history] == ["alice's message"]
    assert [e["text"] for e in bob_history] == ["bob's message"]


# ===================== Notebook author from session, not free text =====================

def test_notebook_author_comes_from_logged_in_user(app_module, client):
    create_and_login(client, user_id="alice", name="Alice")
    resp = client.post("/api/notebook", json={"content": "today was nice"})
    assert resp.status_code == 200
    entries = client.get("/api/notebook").json()
    assert entries[0]["author"] == "Alice"
    assert entries[0]["content"] == "today was nice"
