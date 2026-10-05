"""Extensions (petit_app/extensions.py): loading, menu entries, failure isolation."""

import shutil
import sys
from pathlib import Path

import pytest
from conftest import _AppModules, _drop_app_modules, create_and_login

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "extensions" / "hello.py"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Write extension files into the data dir first, then import the app."""
    monkeypatch.setenv("PETIT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PETIT_APP_EXTENSIONS_DIR", raising=False)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    clients = []

    def _make(files: dict[str, str] | None = None, copy_example: bool = False):
        from fastapi.testclient import TestClient
        ext_dir = tmp_path / "app_extensions"
        if files or copy_example:
            ext_dir.mkdir(exist_ok=True)
        for name, body in (files or {}).items():
            (ext_dir / name).write_text(body, encoding="utf-8")
        if copy_example:
            shutil.copy(EXAMPLE, ext_dir / "hello.py")
        _drop_app_modules()
        import main
        c = TestClient(_AppModules(main).app)
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)
    _drop_app_modules()


def test_no_extensions_dir(make_client):
    c = make_client()
    create_and_login(c)
    assert c.get("/api/extensions").json() == {"loaded": [], "failed": []}
    assert c.get("/api/extensions/nav").json() == []


def test_example_extension_requires_login(make_client):
    c = make_client(copy_example=True)
    create_and_login(c, name="Alice")
    assert c.get("/ext/hello").json() == {"hello": "Alice"}
    assert c.get("/api/extensions").json()["loaded"] == ["hello.py"]
    c.cookies.clear()
    assert c.get("/ext/hello", follow_redirects=False).status_code in (302, 307)
    assert c.get("/api/extensions").status_code == 401


def test_nav_entries_sorted_by_order(make_client):
    c = make_client({
        "a.py": "def register(app, ctx):\n    ctx.add_nav('Later', '/ext/later', order=200)\n",
        "b.py": "def register(app, ctx):\n    ctx.add_nav('First', '/ext/first', order=10)\n",
    })
    create_and_login(c)
    assert c.get("/api/extensions/nav").json() == [
        {"label": "First", "path": "/ext/first"},
        {"label": "Later", "path": "/ext/later"},
    ]


def test_failing_extension_does_not_stop_the_app(make_client, capsys):
    c = make_client({
        "bad.py": "def register(app, ctx):\n    ctx.add_nav('Bad', '/ext/bad')\n    raise ValueError('boom')\n",
        "good.py": "def register(app, ctx):\n    ctx.add_nav('Good', '/ext/good')\n",
    })
    create_and_login(c)
    body = c.get("/api/extensions").json()
    assert body["loaded"] == ["good.py"]
    assert body["failed"] == [{"file": "bad.py", "error": "boom"}]
    assert c.get("/api/extensions/nav").json() == [{"label": "Good", "path": "/ext/good"}]
    assert "extension bad.py failed: boom" in capsys.readouterr().err


def test_extension_cannot_override_a_base_route(make_client, capsys):
    c = make_client({
        "clash.py": (
            "def register(app, ctx):\n"
            "    @app.get('/api/me')\n"
            "    async def fake_me():\n"
            "        return {'id': 'intruder'}\n"
            "    @app.get('/ext/also')\n"
            "    async def also():\n"
            "        return {}\n"
        ),
    })
    create_and_login(c, user_id="alice")
    assert c.get("/api/me").json()["id"] == "alice"
    assert c.get("/ext/also").status_code == 404
    body = c.get("/api/extensions").json()
    assert body["loaded"] == []
    assert body["failed"][0]["file"] == "clash.py"
    assert "extension clash.py skipped: route conflict GET /api/me" in capsys.readouterr().err


def test_underscore_files_are_not_loaded(make_client):
    c = make_client({"_helper.py": "raise RuntimeError('must not be imported')\n"})
    create_and_login(c)
    assert c.get("/api/extensions").json() == {"loaded": [], "failed": []}
