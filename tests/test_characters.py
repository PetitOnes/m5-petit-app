"""Tests for Phase A character discovery and path resolution.

These exercise `list_characters()` / `resolve_character()` / `char_dir()` in
main.py, plus the character-scoped API surface, against a throwaway
PETIT_DATA_DIR (never the real ~/petit_data).

`app_module` / `client` fixtures and `create_and_login()` live in conftest.py
(shared with test_auth.py). Character-scoped API endpoints require a logged-
in session as of Phase B, so `TestApi` logs in first.
"""

from __future__ import annotations

from conftest import create_and_login, make_character as _make_character


def test_list_characters_empty_when_no_dir(app_module, tmp_path):
    assert app_module.list_characters() == []


def test_list_characters_reads_config(app_module, tmp_path):
    _make_character(tmp_path, "alpha", name="アルファ", color="#fff262", m5_hosts="10.0.0.1, 10.0.0.2")
    _make_character(tmp_path, "beta", name="ベータ", color="#00afcc")

    chars = {c["id"]: c for c in app_module.list_characters()}
    assert set(chars) == {"alpha", "beta"}
    assert chars["alpha"]["name"] == "アルファ"
    assert chars["alpha"]["color"] == "#fff262"
    assert chars["alpha"]["m5_hosts"] == ["10.0.0.1", "10.0.0.2"]
    assert chars["beta"]["m5_hosts"] == []


def test_list_characters_falls_back_to_dirname_without_config(app_module, tmp_path):
    _make_character(tmp_path, "no_config_char", with_config=False)

    chars = {c["id"]: c for c in app_module.list_characters()}
    assert "no_config_char" in chars
    assert chars["no_config_char"]["name"] == "no_config_char"
    assert chars["no_config_char"]["color"] == app_module.DEFAULT_CHARACTER_COLOR


def test_resolve_character_found_and_missing(app_module, tmp_path):
    _make_character(tmp_path, "alpha", name="アルファ")

    found = app_module.resolve_character("alpha")
    assert found is not None
    assert found["id"] == "alpha"

    assert app_module.resolve_character("does_not_exist") is None


def test_char_dir_is_under_data_dir_characters(app_module, tmp_path):
    assert app_module.char_dir("alpha") == tmp_path / "characters" / "alpha"


def test_chat_log_is_isolated_per_character(app_module, tmp_path):
    _make_character(tmp_path, "alpha")
    _make_character(tmp_path, "beta")

    app_module._append_chat("alpha", "test_user", "test_user", "hello alpha")
    app_module._append_chat("beta", "test_user", "test_user", "hello beta")

    alpha_log = app_module._load_chat_log("alpha", "test_user")
    beta_log = app_module._load_chat_log("beta", "test_user")
    assert [e["text"] for e in alpha_log] == ["hello alpha"]
    assert [e["text"] for e in beta_log] == ["hello beta"]

    # And the files actually live under each character's own directory,
    # split per user (Phase B).
    assert app_module._chat_log_file("alpha", "test_user") == tmp_path / "characters" / "alpha" / "chat_histories" / "test_user.json"
    assert app_module._chat_log_file("beta", "test_user") == tmp_path / "characters" / "beta" / "chat_histories" / "test_user.json"


def test_album_dir_is_nested_under_character(app_module, tmp_path):
    _make_character(tmp_path, "alpha")
    d = app_module._album_dir("alpha", "test_user")
    assert d == tmp_path / "characters" / "alpha" / "album" / "test_user"
    assert d.is_dir()


class TestApi:
    def test_api_characters_returns_configured_characters(self, app_module, tmp_path, client):
        _make_character(tmp_path, "alpha", name="アルファ", color="#fff262")
        _make_character(tmp_path, "beta", name="ベータ", color="#00afcc")
        create_and_login(client)

        resp = client.get("/api/characters")
        assert resp.status_code == 200
        ids = {c["id"] for c in resp.json()}
        assert ids == {"alpha", "beta"}

    def test_chat_history_is_isolated_per_character_via_api(self, app_module, tmp_path, client):
        _make_character(tmp_path, "alpha")
        _make_character(tmp_path, "beta")
        create_and_login(client, user_id="alice")
        app_module._append_chat("alpha", "alice", "alice", "hi alpha")

        alpha_resp = client.get("/api/alpha/chat/history")
        beta_resp = client.get("/api/beta/chat/history")
        assert [e["text"] for e in alpha_resp.json()] == ["hi alpha"]
        assert beta_resp.json() == []

    def test_unknown_character_is_404(self, app_module, tmp_path, client):
        create_and_login(client)
        resp = client.get("/api/does-not-exist/chat/history")
        assert resp.status_code == 404

    def test_index_page_has_no_hardcoded_character_id(self, client):
        create_and_login(client)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "CHARACTER_ID" not in resp.text
        assert "USER_ID" not in resp.text
        assert "char-tabs" in resp.text
