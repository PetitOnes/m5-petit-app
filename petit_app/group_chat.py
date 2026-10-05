"""Group chat API (sequential NDJSON streaming)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import config
from .auth import get_current_user
from .characters import list_characters
from .chat import _append_chat
from .locks import call_claude

router = APIRouter()


# ===================== Group chat API (会話：グループ, Phase C) =====================
# Every character the logged-in user can see, that opts in via config.json's
# `in_group` flag (default true), gets the message in turn — each one
# streamed back to the client (NDJSON) as soon as it replies, per the design
# doc's "sequential streaming" decision. Each turn is told this is a group
# conversation and gets the previous character's reply as context, so the
# conversation reads as one continuous exchange rather than N independent
# one-off replies.
#
# The log is per logged-in user (design decision ⑤): user A's group chat is
# not visible to user B, even though they may share every character.

def _group_log_file(user_id: str) -> Path:
    return config.DATA_DIR / "users" / user_id / "group_chat.json"


def _load_group_log(user_id: str) -> list[dict]:
    f = _group_log_file(user_id)
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_group_log(user_id: str, entry: dict) -> None:
    f = _group_log_file(user_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    log = _load_group_log(user_id)
    log.append(entry)
    f.write_text(json.dumps(log[-200:], ensure_ascii=False, indent=2), encoding="utf-8")


def _group_characters(user: dict) -> list[dict]:
    return [c for c in list_characters(user) if c.get("in_group", True)]


class GroupChatRequest(BaseModel):
    message: str


async def _group_chat_stream(chars: list[dict], message: str, user: dict):
    """Ask each character in turn, yielding one NDJSON line as soon as it
    replies (returned-first order, not necessarily list order — though here
    it's sequential so they're the same)."""
    now = datetime.now(config.TZ).isoformat()
    user_name = user.get("name") or user["id"]
    user_color = user.get("color") or config.DEFAULT_USER_COLOR
    _append_group_log(user["id"], {"role": "user", "name": user_name, "color": user_color, "text": message, "timestamp": now})
    responses: list[dict] = []
    for char in chars:
        if responses:
            prev = responses[-1]
            context = (
                f"{message}\n\n"
                f"[これはグループ会話です。他の子の返答もこの後に続きます。"
                f"さっき{prev['name']}がこう言っていました]\n{prev['reply']}"
            )
        else:
            context = f"{message}\n\n[これはグループ会話です。他の子の返答もこの後に続きます。]"
        reply = await call_claude(char, context, user=user, source="group")
        _append_chat(char["id"], user["id"], user["id"], message)
        _append_chat(char["id"], user["id"], char["id"], reply)
        r = {
            "character_id": char["id"],
            "name": char.get("name") or char["id"],
            "color": char.get("color") or config.DEFAULT_CHARACTER_COLOR,
            "reply": reply,
        }
        responses.append(r)
        _append_group_log(user["id"], {
            "role": char["id"], "name": r["name"], "color": r["color"],
            "text": reply, "timestamp": datetime.now(config.TZ).isoformat(),
        })
        yield json.dumps(r, ensure_ascii=False) + "\n"


@router.post("/api/group/chat/stream")
async def api_group_chat_stream(req: GroupChatRequest, user: dict = Depends(get_current_user)):
    chars = _group_characters(user)
    return StreamingResponse(
        _group_chat_stream(chars, req.message, user),
        media_type="application/x-ndjson",
    )


@router.get("/api/group/history")
async def api_group_history(user: dict = Depends(get_current_user)):
    return _load_group_log(user["id"])[-200:]
