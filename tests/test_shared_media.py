"""House-wide album / voice memo routes (petit_app/shared_media.py) and the
internal token that lets a local tool (m5-petit-mcp) call them without a login."""

import base64
import io

from conftest import create_and_login, make_character
from PIL import Image


def _jpeg_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 120, 60)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def _token_headers(app_module) -> dict:
    return {app_module.config.INTERNAL_TOKEN_HEADER: app_module.auth.get_internal_token()}


def test_token_file_is_created_at_startup(app_module, client, tmp_path):
    f = tmp_path / ".internal_token"
    assert f.exists() and len(f.read_text().strip()) == 64
    assert (f.stat().st_mode & 0o077) == 0


def test_album_round_trip_with_internal_token(app_module, client, tmp_path):
    make_character(tmp_path, "mio", name="Mio")
    create_and_login(client)
    client.cookies.clear()
    h = _token_headers(app_module)

    r = client.post("/api/album/snapshot", json={"person_id": "mio", "title": "sky", "image_b64": _jpeg_b64()}, headers=h)
    assert r.status_code == 200, r.text
    fname = r.json()["filename"]
    assert (tmp_path / "photo_album" / "mio" / fname).exists()

    photos = client.get("/api/album/mio", headers=h).json()
    assert [p["filename"] for p in photos] == [fname]
    assert photos[0]["read_by"] == [] and photos[0]["locked"] is False

    assert client.get(f"/api/album/mio/{fname}", headers=h).headers["content-type"] == "image/jpeg"
    assert client.post(f"/api/album/mio/{fname}/read", params={"viewer": "mio"}, headers=h).json()["read_by"] == ["mio"]
    assert client.post(f"/api/album/mio/{fname}/lock", headers=h).json()["locked"] is True
    assert client.delete(f"/api/album/mio/{fname}", headers=h).json() == {"ok": True}
    assert client.get("/api/album/mio", headers=h).json() == []


def test_voice_memo_round_trip_with_internal_token(app_module, client, tmp_path):
    make_character(tmp_path, "mio", name="Mio")
    create_and_login(client)
    client.cookies.clear()
    h = _token_headers(app_module)

    r = client.post("/api/voice_memo/mio/upload", data={"title": "hello"},
                    files={"file": ("memo.wav", b"RIFF0000WAVE", "audio/wav")}, headers=h)
    assert r.status_code == 200, r.text
    fname = r.json()["filename"]
    assert (tmp_path / "voice_memo" / "mio" / fname).exists()

    assert [m["filename"] for m in client.get("/api/voice_memo/mio", headers=h).json()] == [fname]
    assert client.get(f"/api/voice_memo/mio/{fname}", headers=h).content == b"RIFF0000WAVE"
    assert client.post(f"/api/voice_memo/mio/{fname}/listen", params={"listener": "mio"}, headers=h).json()["listened_by"] == ["mio"]
    assert client.get("/api/voice_memo/mio", params={"unlistened_by": "mio"}, headers=h).json() == []
    assert client.post(f"/api/voice_memo/mio/{fname}/lock", headers=h).json()["locked"] is True


def test_no_token_and_wrong_token_are_rejected(app_module, client, tmp_path):
    make_character(tmp_path, "mio")
    create_and_login(client)
    client.cookies.clear()
    assert client.get("/api/album/mio").status_code == 401
    assert client.get("/api/album/mio", headers={app_module.config.INTERNAL_TOKEN_HEADER: "wrong"}).status_code == 401


def test_internal_token_opens_nothing_else(app_module, client, tmp_path):
    make_character(tmp_path, "mio")
    create_and_login(client)
    client.cookies.clear()
    h = _token_headers(app_module)
    assert client.get("/api/me", headers=h).status_code == 401
    assert client.get("/api/characters", headers=h).status_code == 401
    assert client.get("/api/mio/album/mio", headers=h).status_code == 401


def test_logged_in_user_can_use_shared_routes(app_module, client, tmp_path):
    make_character(tmp_path, "mio")
    create_and_login(client, user_id="alice")
    r = client.post("/api/album/snapshot", json={"person_id": "alice", "title": "me", "image_b64": _jpeg_b64()})
    assert r.status_code == 200
    assert len(client.get("/api/album/alice").json()) == 1
    assert client.get("/api/album/mio").json() == []


def test_unknown_person_is_rejected_and_no_folder_is_made(app_module, client, tmp_path):
    create_and_login(client)
    h = _token_headers(app_module)
    assert client.get("/api/album/nobody", headers=h).status_code == 400
    assert client.get("/api/voice_memo/..", headers=h).status_code in (400, 404)
    r = client.post("/api/album/snapshot", json={"person_id": "nobody", "title": "x", "image_b64": _jpeg_b64()}, headers=h)
    assert r.status_code == 400
    assert not (tmp_path / "photo_album" / "nobody").exists()


def test_per_character_routes_still_use_their_own_folder(app_module, client, tmp_path):
    make_character(tmp_path, "mio")
    create_and_login(client, user_id="alice")
    r = client.post("/api/mio/album/snapshot", json={"person_id": "alice", "title": "t", "image_b64": _jpeg_b64()})
    assert r.status_code == 200
    assert (tmp_path / "characters" / "mio" / "album" / "alice" / r.json()["filename"]).exists()
    assert client.get("/api/album/alice").json() == []


def test_shared_dirs_can_be_overridden(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    from conftest import _AppModules, _drop_app_modules
    from fastapi.testclient import TestClient
    monkeypatch.setenv("PETIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PETIT_ALBUM_DIR", str(tmp_path / "elsewhere"))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    _drop_app_modules()
    import main
    mods = _AppModules(main)
    make_character(tmp_path / "data", "mio")
    with TestClient(mods.app) as c:
        create_and_login(c)
        r = c.post("/api/album/snapshot", json={"person_id": "mio", "title": "t", "image_b64": _jpeg_b64()})
        assert r.status_code == 200
        assert (tmp_path / "elsewhere" / "mio" / r.json()["filename"]).exists()
    _drop_app_modules()
