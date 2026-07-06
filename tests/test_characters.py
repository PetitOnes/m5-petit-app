"""Tests for Phase A character discovery and path resolution.

These exercise `list_characters()` / `resolve_character()` / `char_dir()` in
main.py, plus the character-scoped API surface, against a throwaway
PETIT_DATA_DIR (never the real ~/petit_data).
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def _make_character(base: Path, char_id: str, name: str | None = None, color: str | None = None,
                     m5_hosts=None, with_config: bool = True) -> None:
    cfg_dir = base / "characters" / char_id / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    if with_config:
        cfg = {"id": char_id}
        if name is not None:
            cfg["name"] = name
        if color is not None:
            cfg["color"] = color
        if m5_hosts is not None:
            cfg["m5_hosts"] = m5_hosts
        (cfg_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    """Import main.py fresh with PETIT_DATA_DIR pointed at a tmp_path."""
    monkeypatch.setenv("PETIT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("USER_ID", "test_user")
    monkeypatch.delenv("CHARACTER_ID", raising=False)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.modules.pop("main", None)
    import main
    importlib.reload(main)
    yield main
    sys.modules.pop("main", None)


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

    app_module._append_chat("alpha", "test_user", "hello alpha")
    app_module._append_chat("beta", "test_user", "hello beta")

    alpha_log = app_module._load_chat_log("alpha")
    beta_log = app_module._load_chat_log("beta")
    assert [e["text"] for e in alpha_log] == ["hello alpha"]
    assert [e["text"] for e in beta_log] == ["hello beta"]

    # And the files actually live under each character's own directory.
    assert app_module._chat_log_file("alpha") == tmp_path / "characters" / "alpha" / "chat_histories" / "chat_history.json"
    assert app_module._chat_log_file("beta") == tmp_path / "characters" / "beta" / "chat_histories" / "chat_history.json"


def test_album_dir_is_nested_under_character(app_module, tmp_path):
    _make_character(tmp_path, "alpha")
    d = app_module._album_dir("alpha", "test_user")
    assert d == tmp_path / "characters" / "alpha" / "album" / "test_user"
    assert d.is_dir()


class TestApi:
    @pytest.fixture
    def client(self, app_module):
        from fastapi.testclient import TestClient
        with TestClient(app_module.app) as c:
            yield c

    def test_api_characters_returns_configured_characters(self, app_module, tmp_path, client):
        _make_character(tmp_path, "alpha", name="アルファ", color="#fff262")
        _make_character(tmp_path, "beta", name="ベータ", color="#00afcc")

        resp = client.get("/api/characters")
        assert resp.status_code == 200
        ids = {c["id"] for c in resp.json()}
        assert ids == {"alpha", "beta"}

    def test_chat_history_is_isolated_per_character_via_api(self, app_module, tmp_path, client):
        _make_character(tmp_path, "alpha")
        _make_character(tmp_path, "beta")
        app_module._append_chat("alpha", "test_user", "hi alpha")

        alpha_resp = client.get("/api/alpha/chat/history")
        beta_resp = client.get("/api/beta/chat/history")
        assert [e["text"] for e in alpha_resp.json()] == ["hi alpha"]
        assert beta_resp.json() == []

    def test_unknown_character_is_404(self, app_module, tmp_path, client):
        resp = client.get("/api/does-not-exist/chat/history")
        assert resp.status_code == 404

    def test_index_page_has_no_hardcoded_character_id(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "CHARACTER_ID" not in resp.text
        assert "char-tabs" in resp.text
