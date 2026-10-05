"""Chat API (per character x user)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from . import config
from .auth import get_current_user
from .characters import char_dir, require_character
from .locks import call_claude, character_lock_status

router = APIRouter()


# ===================== Chat API (会話) =====================
# Phase B: chat history and Claude session state are split per (character,
# user) pair — "talking with Alice" and "talking with Bob" are different
# continuous contexts for the same character (see call_claude()'s docstring
# for the session-id half of this).

def _chat_log_file(character_id: str, user_id: str) -> Path:
    return char_dir(character_id) / "chat_histories" / f"{user_id}.json"


def _load_chat_log(character_id: str, user_id: str) -> list[dict]:
    f = _chat_log_file(character_id, user_id)
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_chat_log(character_id: str, user_id: str, log: list[dict]) -> None:
    f = _chat_log_file(character_id, user_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(log[-200:], ensure_ascii=False, indent=2), encoding="utf-8")


def _append_chat(character_id: str, user_id: str, role: str, text: str) -> None:
    log = _load_chat_log(character_id, user_id)
    log.append({"role": role, "text": text, "timestamp": datetime.now(config.TZ).isoformat()})
    _save_chat_log(character_id, user_id, log)


def _chat_user_ids(character_id: str) -> list[str]:
    """Every user id that has a chat_histories/<user_id>.json with this
    character (used to aggregate a day's conversations across users, e.g.
    for the diary)."""
    d = char_dir(character_id) / "chat_histories"
    if not d.is_dir():
        return []
    return [f.stem for f in d.glob("*.json")]


class ChatRequest(BaseModel):
    message: str


@router.post("/api/{character_id}/chat")
async def api_chat(character_id: str, req: ChatRequest, user: dict = Depends(get_current_user)):
    char = require_character(character_id, user)
    _append_chat(character_id, user["id"], user["id"], req.message)
    reply = await call_claude(char, req.message, user=user, source="chat")
    _append_chat(character_id, user["id"], character_id, reply)
    return {"reply": reply}


@router.get("/api/{character_id}/chat/history")
async def api_chat_history(character_id: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    return _load_chat_log(character_id, user["id"])[-100:]


@router.get("/api/{character_id}/chat/status")
async def api_chat_status(character_id: str, user: dict = Depends(get_current_user)):
    """Polled by the UI while a chat request is in flight, so it can show
    '<name>と話し中…' instead of a bare spinner when the character's lock is
    held by someone else (Phase C: one character = one mind)."""
    require_character(character_id, user)
    holder = character_lock_status(character_id)
    if holder and holder["user_id"] != user["id"]:
        return {"busy": True, "partner_name": holder["name"]}
    return {"busy": False, "partner_name": None}
