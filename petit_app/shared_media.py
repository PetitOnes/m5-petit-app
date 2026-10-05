"""House-wide album / voice memo API: one folder per person.

These are the routes the body MCP server (m5-petit-mcp) calls:

    /api/album/snapshot          /api/album/{person_id}[/{filename}[/read|/lock]]
    /api/voice_memo/{person_id}[/upload|/{filename}[/listen|/lock]]

A "person" is a character or a user. Photos and memos live in
`PETIT_ALBUM_DIR/<person_id>/` and `PETIT_VOICE_MEMO_DIR/<person_id>/`
(default `$PETIT_DATA_DIR/photo_album` / `voice_memo`).

Callers are either a logged-in user, or a local tool that sends the internal
token (see auth.get_internal_token). The per-character routes in album.py /
voice_memo.py are unchanged; both share the same handlers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from . import album, config, voice_memo
from .auth import find_user, get_caller
from .characters import resolve_character

router = APIRouter()


def _require_person(person_id: str, caller: dict) -> None:
    """`person_id` must be a character the caller can see, or a user."""
    if config._ID_RE.match(person_id):
        user = None if caller.get("internal") else caller
        if resolve_character(person_id, user=user) is not None or find_user(person_id) is not None:
            return
    raise HTTPException(status_code=400, detail="unknown person")


# ===================== Album =====================

@router.post("/api/album/snapshot")
async def shared_album_snapshot(body: album.AlbumSnapshotBody, caller: dict = Depends(get_caller)):
    _require_person(body.person_id, caller)
    return await album.api_album_snapshot(None, body, user=caller)


@router.get("/api/album/{person_id}")
async def shared_album_list(person_id: str, caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await album.api_album_list(None, person_id, user=caller)


@router.get("/api/album/{person_id}/{filename}")
async def shared_album_image(person_id: str, filename: str, caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await album.api_album_image(None, person_id, filename, user=caller)


@router.post("/api/album/{person_id}/upload")
async def shared_album_upload(person_id: str, file: UploadFile = File(...), title: str = Form("photo"),
                              caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await album.api_album_upload(None, person_id, file=file, title=title, user=caller)


@router.post("/api/album/{person_id}/{filename}/read")
async def shared_album_mark_read(person_id: str, filename: str, viewer: str, caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await album.api_album_mark_read(None, person_id, filename, viewer, user=caller)


@router.post("/api/album/{person_id}/{filename}/lock")
async def shared_album_toggle_lock(person_id: str, filename: str, caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await album.api_album_toggle_lock(None, person_id, filename, user=caller)


@router.delete("/api/album/{person_id}/{filename}")
async def shared_album_delete(person_id: str, filename: str, caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await album.api_album_delete(None, person_id, filename, user=caller)


# ===================== Voice memo =====================

@router.get("/api/voice_memo/{person_id}")
async def shared_voice_memo_list(person_id: str, unlistened_by: str = "", caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await voice_memo.api_voice_memo_list(None, person_id, unlistened_by=unlistened_by, user=caller)


@router.get("/api/voice_memo/{person_id}/{filename}")
async def shared_voice_memo_file(person_id: str, filename: str, caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await voice_memo.api_voice_memo_file(None, person_id, filename, user=caller)


@router.post("/api/voice_memo/{person_id}/upload")
async def shared_voice_memo_upload(person_id: str, file: UploadFile = File(...), title: str = Form("memo"),
                                   caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await voice_memo.api_voice_memo_upload(None, person_id, file=file, title=title, user=caller)


@router.post("/api/voice_memo/{person_id}/{filename}/listen")
async def shared_voice_memo_mark_listen(person_id: str, filename: str, listener: str,
                                        caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await voice_memo.api_voice_memo_mark_listen(None, person_id, filename, listener, user=caller)


@router.post("/api/voice_memo/{person_id}/{filename}/lock")
async def shared_voice_memo_toggle_lock(person_id: str, filename: str, caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await voice_memo.api_voice_memo_toggle_lock(None, person_id, filename, user=caller)


@router.delete("/api/voice_memo/{person_id}/{filename}")
async def shared_voice_memo_delete(person_id: str, filename: str, caller: dict = Depends(get_caller)):
    _require_person(person_id, caller)
    return await voice_memo.api_voice_memo_delete(None, person_id, filename, user=caller)
