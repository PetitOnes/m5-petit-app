"""Configuration: environment variables, data-directory layout, constants."""

from __future__ import annotations

import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

VOICE_API_HOST = os.environ.get("VOICE_API_HOST", "")
_ASR_URL = f"http://{VOICE_API_HOST}:8765" if VOICE_API_HOST else ""

DATA_DIR = Path(os.environ.get("PETIT_DATA_DIR", Path.home() / "petit_data"))
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).parent.parent))
SCRIPTS_DIR = PROJECT_DIR / "scripts"

CHARACTERS_DIR = DATA_DIR / "characters"
MAILBOX_DIR = DATA_DIR / "mailbox"
NOTEBOOK_FILE = DATA_DIR / "notebook" / "notebook.json"
MAILBOX_METADATA_FILE = DATA_DIR / "mailbox" / ".metadata.json"
USERS_FILE = DATA_DIR / "users.json"
SESSION_SECRET_FILE = DATA_DIR / ".session_secret"
# House-wide album / voice memo store, one folder per person (a character or a
# user). This is what the body MCP server (m5-petit-mcp) reads and writes.
SHARED_ALBUM_DIR = Path(os.environ.get("PETIT_ALBUM_DIR", DATA_DIR / "photo_album"))
SHARED_VOICE_MEMO_DIR = Path(os.environ.get("PETIT_VOICE_MEMO_DIR", DATA_DIR / "voice_memo"))
# Shared secret for local tools (the MCP server) that call the dashboard
# without a login session. Created on first start, readable only by the owner.
INTERNAL_TOKEN_FILE = DATA_DIR / ".internal_token"
INTERNAL_TOKEN_HEADER = "X-Petit-Internal-Token"

DEFAULT_CHARACTER_COLOR = "#4a7c59"
DEFAULT_USER_COLOR = "#7da8f5"

ALBUM_MAX_PHOTOS = 50
ALBUM_MAX_PX = 1200
ALBUM_JPEG_QUALITY = 80

VOICE_MEMO_MAX = 20
VOICE_MEMO_MAX_BYTES = 6 * 1024 * 1024  # ~30 sec

_AUDIO_EXTS = {".webm", ".wav", ".ogg", ".mp4", ".m4a"}

TZ = ZoneInfo("Asia/Tokyo")

SESSION_COOKIE_NAME = "petit_session"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days
_ID_RE = re.compile(r"^[a-zA-Z0-9_]+$")

# The claude CLI binary to invoke — overridable so tests can point it at a
# fake script instead of shelling out to the real `claude` (see call_claude()).
CLAUDE_CLI_PATH = os.environ.get("CLAUDE_CLI_PATH", "claude")

# "1 character = 1 mind" (design principle #4): a character's Claude session
# is serialized behind a per-character lock so it never carries on two
# conversations at once. See _character_lock() below.
CHAR_LOCK_TIMEOUT = 120.0  # seconds
