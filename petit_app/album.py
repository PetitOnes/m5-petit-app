"""Album helpers and API."""

from __future__ import annotations

import base64
import io
import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageOps
from pydantic import BaseModel

from . import config
from .auth import get_current_user
from .characters import char_dir, require_character

# ===================== Album helpers =====================

def _album_dir(character_id: str | None, person_id: str) -> Path:
    if character_id is None:
        d = config.SHARED_ALBUM_DIR / person_id
    else:
        d = char_dir(character_id) / "album" / person_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _compress_image(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > config.ALBUM_MAX_PX:
        img.thumbnail((config.ALBUM_MAX_PX, config.ALBUM_MAX_PX), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=config.ALBUM_JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def _album_filename(person_id: str, title: str) -> str:
    now = datetime.now(config.TZ)
    safe = re.sub(r"[^\w-]", "_", title)[:40]
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{person_id}_{safe}.jpg"


def _album_reads_file(character_id: str, person_id: str) -> Path:
    return _album_dir(character_id, person_id) / ".reads.json"


def _load_album_reads(character_id: str, person_id: str) -> dict:
    f = _album_reads_file(character_id, person_id)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def _save_album_reads(character_id: str, person_id: str, data: dict):
    _album_reads_file(character_id, person_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _album_locks_file(character_id: str, person_id: str) -> Path:
    return _album_dir(character_id, person_id) / ".locks.json"


def _load_album_locks(character_id: str, person_id: str) -> set:
    f = _album_locks_file(character_id, person_id)
    try:
        return set(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else set()
    except Exception:
        return set()


def _save_album_locks(character_id: str, person_id: str, locks: set):
    _album_locks_file(character_id, person_id).write_text(json.dumps(list(locks), ensure_ascii=False), encoding="utf-8")


def _prune_album(character_id: str, person_id: str):
    d = _album_dir(character_id, person_id)
    locks = _load_album_locks(character_id, person_id)
    photos = sorted(d.glob("*.jpg"))
    while len(photos) > config.ALBUM_MAX_PHOTOS:
        target = next((p for p in photos if p.name not in locks), None)
        if target is None:
            break
        target.unlink(missing_ok=True)
        photos.remove(target)


def _check_scope(character_id: str | None, user: dict) -> None:
    """Per-character routes: the character must be visible to the user.
    House-wide routes (character_id None) are checked in shared_media.py."""
    if character_id is not None:
        require_character(character_id, user)


router = APIRouter()


@router.get("/api/{character_id}/album/{person_id}")
async def api_album_list(character_id: str, person_id: str, user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    d = _album_dir(character_id, person_id)
    reads = _load_album_reads(character_id, person_id)
    locks = _load_album_locks(character_id, person_id)
    photos = []
    for f in sorted(d.glob("*.jpg"), reverse=True):
        photos.append({
            "filename": f.name,
            "size": f.stat().st_size,
            "mtime": f.stat().st_mtime,
            "read_by": reads.get(f.name, []),
            "locked": f.name in locks,
        })
    return photos


@router.get("/api/{character_id}/album/{person_id}/{filename}")
async def api_album_image(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    path = _album_dir(character_id, person_id) / filename
    if not path.exists() or not path.name.endswith(".jpg"):
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(content=path.read_bytes(), media_type="image/jpeg")


@router.post("/api/{character_id}/album/{person_id}/upload")
async def api_album_upload(character_id: str, person_id: str, file: UploadFile = File(...), title: str = Form("photo"), user: dict = Depends(get_current_user)):
    """Upload a photo (e.g. from a smartphone)."""
    _check_scope(character_id, user)
    data = await file.read()
    compressed = _compress_image(data)
    fname = _album_filename(person_id, title)
    (_album_dir(character_id, person_id) / fname).write_bytes(compressed)
    _prune_album(character_id, person_id)
    return {"ok": True, "filename": fname}


class AlbumSnapshotBody(BaseModel):
    person_id: str
    title: str
    image_b64: str


@router.post("/api/{character_id}/album/snapshot")
async def api_album_snapshot(character_id: str, body: AlbumSnapshotBody, user: dict = Depends(get_current_user)):
    """Save a base64-encoded JPEG snapshot (called by MCP server)."""
    _check_scope(character_id, user)
    raw = base64.b64decode(body.image_b64)
    compressed = _compress_image(raw)
    fname = _album_filename(body.person_id, body.title)
    (_album_dir(character_id, body.person_id) / fname).write_bytes(compressed)
    _prune_album(character_id, body.person_id)
    return {"ok": True, "filename": fname}


@router.post("/api/{character_id}/album/{person_id}/{filename}/read")
async def api_album_mark_read(character_id: str, person_id: str, filename: str, viewer: str, user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    reads = _load_album_reads(character_id, person_id)
    viewers = reads.get(filename, [])
    if viewer not in viewers:
        viewers.append(viewer)
        reads[filename] = viewers
        _save_album_reads(character_id, person_id, reads)
    return {"ok": True, "read_by": viewers}


@router.post("/api/{character_id}/album/{person_id}/{filename}/lock")
async def api_album_toggle_lock(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    path = _album_dir(character_id, person_id) / filename
    if not path.exists() or not filename.endswith(".jpg"):
        return JSONResponse({"error": "not found"}, status_code=404)
    locks = _load_album_locks(character_id, person_id)
    if filename in locks:
        locks.discard(filename)
        locked = False
    else:
        locks.add(filename)
        locked = True
    _save_album_locks(character_id, person_id, locks)
    return {"ok": True, "locked": locked}


@router.delete("/api/{character_id}/album/{person_id}/{filename}")
async def api_album_delete(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    _check_scope(character_id, user)
    path = _album_dir(character_id, person_id) / filename
    if not path.exists() or not path.name.endswith(".jpg"):
        return JSONResponse({"error": "not found"}, status_code=404)
    path.unlink()
    reads = _load_album_reads(character_id, person_id)
    if filename in reads:
        del reads[filename]
        _save_album_reads(character_id, person_id, reads)
    return {"ok": True}
