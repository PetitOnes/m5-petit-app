"""Shared fixtures for main.py tests.

Every test imports main.py fresh against a throwaway PETIT_DATA_DIR (never
the real ~/petit_data) via the `app_module` fixture, so state from one test
never leaks into another.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


def make_character(base: Path, char_id: str, name: str | None = None, color: str | None = None,
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
    monkeypatch.delenv("CHARACTER_ID", raising=False)
    monkeypatch.delenv("USER_ID", raising=False)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.modules.pop("main", None)
    import main
    importlib.reload(main)
    yield main
    sys.modules.pop("main", None)


@pytest.fixture
def client(app_module):
    from fastapi.testclient import TestClient
    with TestClient(app_module.app) as c:
        yield c


def create_and_login(client, user_id: str = "alice", name: str = "Alice", password: str = "s3cret",
                      color: str = "#7da8f5") -> dict:
    """POST /api/setup (only works while users.json is empty) then log in,
    leaving the session cookie set on `client`. Returns the login response
    body (`{"ok": True, "user": {...}}`)."""
    r = client.post("/api/setup", json={"id": user_id, "name": name, "password": password, "color": color})
    assert r.status_code == 200, r.text
    r = client.post("/api/auth/login", json={"id": user_id, "password": password})
    assert r.status_code == 200, r.text
    return r.json()
