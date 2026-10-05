"""M5 Petit App — dashboard server for N users / N characters.

Phase A: the character side is N-ified. The character list is discovered by
scanning `PETIT_DATA_DIR/characters/*/config/config.json` — no env var, no
restart needed to add a character. See `list_characters()` /
`resolve_character()` below.

Phase B (this file): the human side is N-ified too. `users.json` holds the
family's accounts (id/name/color/password_hash/characters). Cookie-based
sessions (HMAC-signed) gate every page and API call; a first-run setup page
creates the first account when `users.json` doesn't exist yet.
`resolve_character()` / `require_character()` now enforce each user's
`characters` allow-list ("all" or a list of character ids) — a character
outside it 404s, same as one that doesn't exist.

Environment variables:
  VOICE_API_HOST  ASR server host (for mic transcription)
  PETIT_DATA_DIR  Data directory (default: ~/petit_data)
  PROJECT_DIR     Project root for Claude CLI and scripts (default: this file's parent)
  PORT            Port to listen on (default: 8765)

Runs on port 8765 by default. Override with PORT env var or uvicorn args.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import (
    album,
    auth,
    characters,
    chat,
    diary,
    group_chat,
    mailbox,
    notebook,
    records,
    ui_legacy,
    voice_memo,
)
from .auth import auth_gate
from .characters import list_characters
from .m5_watcher import _m5_watcher

# ===================== App lifecycle =====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # One watcher task per character that has M5 hosts configured. Characters
    # added after startup are picked up by the HTTP API immediately, but need
    # a restart to get a watcher (matches Phase A scope — no hot-reload for
    # the websocket side).
    tasks = [asyncio.create_task(_m5_watcher(c)) for c in list_characters() if c.get("m5_hosts")]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(lifespan=lifespan)


app.middleware("http")(auth_gate)

# Registration order = the top-to-bottom order the routes had in the single
# main.py, so path matching is unchanged.
app.include_router(auth.router)
app.include_router(characters.router)
app.include_router(album.router)
app.include_router(voice_memo.router)
app.include_router(notebook.router)
app.include_router(mailbox.router)
app.include_router(chat.router)
app.include_router(group_chat.router)
app.include_router(records.router)
app.include_router(diary.router)
app.include_router(ui_legacy.router)


# ===================== Entry point =====================

def main():
    import uvicorn
    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run("petit_app.main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
