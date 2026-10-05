"""Voice memo helpers and API."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from . import config
from .auth import get_current_user
from .characters import char_dir, require_character

# ===================== Voice memo helpers =====================

def _voice_memo_dir(character_id: str | None, person_id: str) -> Path:
    if character_id is None:
        d = config.SHARED_VOICE_MEMO_DIR / person_id
    else:
        d = char_dir(character_id) / "voice_memo" / person_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _voice_memo_filename(person_id: str, title: str, ext: str = ".wav") -> str:
    now = datetime.now(config.TZ)
    safe = re.sub(r"[^\w-]", "_", title)[:40]
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{person_id}_{safe}{ext}"


def _voice_locks_file(character_id: str, person_id: str) -> Path:
    return _voice_memo_dir(character_id, person_id) / ".locks.json"


def _load_voice_locks(character_id: str, person_id: str) -> set:
    f = _voice_locks_file(character_id, person_id)
    try:
        return set(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else set()
    except Exception:
        return set()


def _save_voice_locks(character_id: str, person_id: str, locks: set):
    _voice_locks_file(character_id, person_id).write_text(json.dumps(list(locks), ensure_ascii=False), encoding="utf-8")


def _listens_file(character_id: str, person_id: str) -> Path:
    return _voice_memo_dir(character_id, person_id) / ".listens.json"


def _load_listens(character_id: str, person_id: str) -> dict:
    f = _listens_file(character_id, person_id)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def _save_listens(character_id: str, person_id: str, data: dict):
    _listens_file(character_id, person_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _prune_voice_memo(character_id: str, person_id: str):
    d = _voice_memo_dir(character_id, person_id)
    locks = _load_voice_locks(character_id, person_id)
    memos = sorted(
        [f for f in d.iterdir() if f.suffix in config._AUDIO_EXTS],
        key=lambda f: f.stat().st_mtime,
    )
    while len(memos) > config.VOICE_MEMO_MAX:
        target = next((f for f in memos if f.name not in locks), None)
        if target is None:
            break
        target.unlink(missing_ok=True)
        memos.remove(target)


def _check_scope(character_id: str | None, user: dict) -> None:
    """Per-character routes: the character must be visible to the user.
    House-wide routes (character_id None) are checked in shared_media.py."""
    if character_id is not None:
        require_character(character_id, user)


router = APIRouter()


@router.get("/api/{character_id}/voice_memo/{person_id}")
async def api_voice_memo_list(character_id: str, person_id: str, unlistened_by: str = "", user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    d = _voice_memo_dir(character_id, person_id)
    listens = _load_listens(character_id, person_id)
    locks = _load_voice_locks(character_id, person_id)
    memos = []
    for f in sorted(
        [x for x in d.iterdir() if x.suffix in config._AUDIO_EXTS],
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    ):
        memos.append({
            "filename": f.name,
            "size": f.stat().st_size,
            "mtime": f.stat().st_mtime,
            "listened_by": listens.get(f.name, []),
            "locked": f.name in locks,
        })
    if unlistened_by:
        memos = [m for m in memos if unlistened_by not in m["listened_by"]]
    return memos


@router.get("/api/{character_id}/voice_memo/{person_id}/{filename}")
async def api_voice_memo_file(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    path = _voice_memo_dir(character_id, person_id) / filename
    if not path.exists() or path.suffix not in config._AUDIO_EXTS:
        return JSONResponse({"error": "not found"}, status_code=404)
    media_types = {".webm": "audio/webm", ".wav": "audio/wav", ".ogg": "audio/ogg",
                   ".mp4": "audio/mp4", ".m4a": "audio/mp4"}
    return Response(content=path.read_bytes(), media_type=media_types.get(path.suffix, "audio/octet-stream"))


@router.post("/api/{character_id}/voice_memo/{person_id}/upload")
async def api_voice_memo_upload(character_id: str, person_id: str, file: UploadFile = File(...), title: str = Form("memo"), user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    data = await file.read()
    if len(data) > config.VOICE_MEMO_MAX_BYTES:
        return JSONResponse({"error": "file too large"}, status_code=400)
    orig_ext = Path(file.filename or "").suffix.lower() if file.filename else ""
    ext = orig_ext if orig_ext in config._AUDIO_EXTS else ".wav"
    fname = _voice_memo_filename(person_id, title, ext)
    (_voice_memo_dir(character_id, person_id) / fname).write_bytes(data)
    _prune_voice_memo(character_id, person_id)
    return {"ok": True, "filename": fname}


@router.post("/api/{character_id}/voice_memo/{person_id}/{filename}/listen")
async def api_voice_memo_mark_listen(character_id: str, person_id: str, filename: str, listener: str, user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    listens = _load_listens(character_id, person_id)
    listeners = listens.get(filename, [])
    if listener not in listeners:
        listeners.append(listener)
        listens[filename] = listeners
        _save_listens(character_id, person_id, listens)
    return {"ok": True, "listened_by": listeners}


@router.post("/api/{character_id}/voice_memo/{person_id}/{filename}/lock")
async def api_voice_memo_toggle_lock(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    path = _voice_memo_dir(character_id, person_id) / filename
    if not path.exists() or path.suffix not in config._AUDIO_EXTS:
        return JSONResponse({"error": "not found"}, status_code=404)
    locks = _load_voice_locks(character_id, person_id)
    if filename in locks:
        locks.discard(filename)
        locked = False
    else:
        locks.add(filename)
        locked = True
    _save_voice_locks(character_id, person_id, locks)
    return {"ok": True, "locked": locked}


@router.delete("/api/{character_id}/voice_memo/{person_id}/{filename}")
async def api_voice_memo_delete(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    path = _voice_memo_dir(character_id, person_id) / filename
    if not path.exists() or path.suffix not in config._AUDIO_EXTS:
        return JSONResponse({"error": "not found"}, status_code=404)
    path.unlink()
    listens = _load_listens(character_id, person_id)
    if filename in listens:
        del listens[filename]
        _save_listens(character_id, person_id, listens)
    return {"ok": True}
