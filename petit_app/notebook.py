"""Notebook API."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from . import config
from .auth import get_current_user

router = APIRouter()


# ===================== Notebook API =====================
# Not character-scoped (a family notebook shared across everyone talking to
# any character) — out of Phase A's scope structurally, but the author name
# (Phase B) now always comes from the logged-in user, not free-text input.

class NotebookEntry(BaseModel):
    content: str


@router.get("/api/notebook")
async def api_notebook_list(user: dict = Depends(get_current_user)):
    if not config.NOTEBOOK_FILE.exists():
        return []
    try:
        entries = json.loads(config.NOTEBOOK_FILE.read_text(encoding="utf-8"))
        entries.reverse()
        return entries
    except Exception:
        return []


@router.post("/api/notebook")
async def api_notebook_add(entry: NotebookEntry, user: dict = Depends(get_current_user)):
    config.NOTEBOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if config.NOTEBOOK_FILE.exists():
        try:
            entries = json.loads(config.NOTEBOOK_FILE.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    now = datetime.now(config.TZ).strftime("%Y/%m/%d %H:%M")
    author = user.get("name") or user["id"]
    entries.append({"author": author, "date": now, "content": entry.content})
    config.NOTEBOOK_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}
