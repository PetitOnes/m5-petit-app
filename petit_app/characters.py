"""Character discovery and resolution (directory is the source of truth)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from . import config
from .auth import _user_can_access, get_current_user

# ===================== Character resolution (Phase A / Phase B) =====================
# Design principle: "the directory is the source of truth" — the character
# list comes from scanning characters/*/config/config.json, never from an env
# var. Adding a character = adding a directory (the M5 websocket watchers are
# only wired up at process startup, but the HTTP API picks up new characters
# immediately).
#
# `resolve_character()` / `require_character()` take an optional `user` (a
# users.json record). When given, characters outside that user's `characters`
# allow-list are treated as not found — a user can't tell "wrong id" from
# "not yours" from the 404 alone.

def list_characters(user: dict | None = None) -> list[dict]:
    """Scan characters/*/config/config.json.

    Returns dicts with id/name/color/m5_hosts/in_group/data_dir. A directory
    without a config.json still shows up, with id/name falling back to the
    directory name.

    `user` restricts the result to that user's visible characters (per
    `users.json`'s `characters: "all" | [...]`). Internal callers that need
    every character regardless of ownership (the M5 watcher startup, the
    diary/records maintenance code) pass `user=None`.
    """
    if not config.CHARACTERS_DIR.is_dir():
        return []
    chars = []
    for d in sorted(config.CHARACTERS_DIR.iterdir()):
        if not d.is_dir():
            continue
        cfg: dict = {}
        cfg_file = d / "config" / "config.json"
        if cfg_file.exists():
            try:
                cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        char_id = cfg.get("id") or d.name
        if user is not None and not _user_can_access(user, char_id):
            continue
        m5_hosts = cfg.get("m5_hosts")
        if m5_hosts is None:
            m5_hosts = cfg.get("m5_host", "")
        if isinstance(m5_hosts, str):
            m5_hosts = [h.strip() for h in m5_hosts.split(",") if h.strip()]
        chars.append({
            "id": char_id,
            "name": cfg.get("name") or char_id,
            "color": cfg.get("color") or config.DEFAULT_CHARACTER_COLOR,
            "m5_hosts": m5_hosts or [],
            "in_group": cfg.get("in_group", True),
            "data_dir": str(d),
        })
    return chars


def resolve_character(character_id: str, user: dict | None = None) -> dict | None:
    """Look up one character by id, honoring `user`'s visible-character
    allow-list when given."""
    for c in list_characters():
        if c["id"] == character_id:
            if user is not None and not _user_can_access(user, character_id):
                return None
            return c
    return None


def require_character(character_id: str, user: dict | None = None) -> dict:
    char = resolve_character(character_id, user=user)
    if char is None:
        raise HTTPException(status_code=404, detail=f"character not found: {character_id}")
    return char


def char_dir(character_id: str) -> Path:
    return config.CHARACTERS_DIR / character_id


router = APIRouter()


# ===================== Characters API =====================

@router.get("/api/characters")
async def api_characters(user: dict = Depends(get_current_user)):
    return list_characters(user)
