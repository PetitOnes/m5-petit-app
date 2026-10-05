"""Diary API."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends

from . import config
from .auth import get_current_user, list_users
from .characters import char_dir, require_character
from .chat import _chat_user_ids, _load_chat_log
from .locks import call_claude

router = APIRouter()


# ===================== Diary API (日記) =====================
# Summarized from the day's chat_log entries (no external memory store
# needed). Phase B: chat history is split per user, so a day's entries are
# gathered across every user who talked to this character that day — the
# diary is the character's day, not any one human's.

def _diary_dir(character_id: str) -> Path:
    return char_dir(character_id) / "diary"


def _chat_entries_for_date(character_id: str, date: str) -> list[dict]:
    entries = []
    for user_id in _chat_user_ids(character_id):
        entries.extend(e for e in _load_chat_log(character_id, user_id) if e["timestamp"].startswith(date))
    entries.sort(key=lambda e: e["timestamp"])
    return entries


async def _generate_diary_summary(character: dict, date: str, entries: list[dict]) -> str:
    if not entries:
        return ""
    convo = "\n".join(f"{e['role']}: {e['text']}" for e in entries)
    users_by_id = {u["id"]: u.get("name") or u["id"] for u in list_users()}
    participant_ids = sorted({e["role"] for e in entries if e["role"] != character["id"]})
    participants = "、".join(users_by_id.get(uid, uid) for uid in participant_ids) or "だれか"
    prompt = (
        f"{date} の{participants}と{character['name']}の会話ログです。{character['name']}の視点で、"
        f"その日の出来事や気持ちを3〜5行の日記としてまとめてください。\n\n{convo}"
    )
    return await call_claude(character, prompt, source="diary")


@router.get("/api/{character_id}/diary/{date}")
async def api_diary(character_id: str, date: str, user: dict = Depends(get_current_user)):
    char = require_character(character_id, user)
    today = datetime.now(config.TZ).strftime("%Y-%m-%d")
    entries = _chat_entries_for_date(character_id, date)
    cache_file = _diary_dir(character_id) / f"{date}.txt"
    if cache_file.exists():
        summary = cache_file.read_text(encoding="utf-8")
    elif date == today:
        # Don't auto-generate today's diary — wait for the manual "書く" button.
        summary = None
    else:
        summary = await _generate_diary_summary(char, date, entries)
        if summary:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(summary, encoding="utf-8")
    return {"date": date, "summary": summary, "count": len(entries)}


@router.post("/api/{character_id}/diary/{date}/summarize")
async def api_diary_summarize(character_id: str, date: str, user: dict = Depends(get_current_user)):
    char = require_character(character_id, user)
    entries = _chat_entries_for_date(character_id, date)
    summary = await _generate_diary_summary(char, date, entries)
    if summary:
        d = _diary_dir(character_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{date}.txt").write_text(summary, encoding="utf-8")
    return {"summary": summary}
