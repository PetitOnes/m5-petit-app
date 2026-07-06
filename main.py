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
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import websockets
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from PIL import Image, ImageOps
from pydantic import BaseModel

# ===================== Config =====================

VOICE_API_HOST = os.environ.get("VOICE_API_HOST", "")
_ASR_URL = f"http://{VOICE_API_HOST}:8765" if VOICE_API_HOST else ""

DATA_DIR = Path(os.environ.get("PETIT_DATA_DIR", Path.home() / "petit_data"))
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).parent))
SCRIPTS_DIR = PROJECT_DIR / "scripts"

CHARACTERS_DIR = DATA_DIR / "characters"
MAILBOX_DIR = DATA_DIR / "mailbox"
NOTEBOOK_FILE = DATA_DIR / "notebook" / "notebook.json"
MAILBOX_METADATA_FILE = DATA_DIR / "mailbox" / ".metadata.json"
USERS_FILE = DATA_DIR / "users.json"
SESSION_SECRET_FILE = DATA_DIR / ".session_secret"

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


# ===================== Users & auth (Phase B) =====================
# `users.json` is the source of truth for the human side, the same way
# characters/*/config/config.json is for the character side (design
# principle: "people are users.json, not an env var"). Each user has:
#   id, name, color, password_hash ("scrypt$n$r$p$salt_hex$hash_hex"),
#   characters ("all" | [character_id, ...])
#
# Sessions are a signed cookie (HMAC-SHA256, not encrypted — there's nothing
# secret in the payload beyond the user id + expiry) so there's no server-side
# session store to manage. The signing key lives in `.session_secret` next to
# users.json, generated on first use and written 0600.


def _load_users_doc() -> dict:
    if not USERS_FILE.exists():
        return {"users": []}
    try:
        doc = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"users": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("users"), list):
        return {"users": []}
    return doc


def _save_users_doc(doc: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        USERS_FILE.chmod(0o600)
    except OSError:
        pass  # best-effort (e.g. unsupported on some filesystems)


def list_users() -> list[dict]:
    """Public user list — never includes password_hash."""
    return [{k: v for k, v in u.items() if k != "password_hash"} for u in _load_users_doc()["users"]]


def find_user(user_id: str) -> dict | None:
    for u in _load_users_doc()["users"]:
        if u.get("id") == user_id:
            return u
    return None


def users_exist() -> bool:
    return bool(_load_users_doc()["users"])


def _hash_password(password: str, *, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, n_s, r_s, p_s, salt_hex, hash_hex = stored_hash.split("$")
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n_s), r=int(r_s), p=int(p_s), dklen=32)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def create_user(user_id: str, name: str, password: str, color: str | None = None,
                 characters: str | list[str] = "all") -> dict:
    if not _ID_RE.match(user_id):
        raise ValueError("user id must be alphanumeric/underscore")
    doc = _load_users_doc()
    if any(u.get("id") == user_id for u in doc["users"]):
        raise ValueError(f"user already exists: {user_id}")
    user = {
        "id": user_id,
        "name": name or user_id,
        "color": color or DEFAULT_USER_COLOR,
        "password_hash": _hash_password(password),
        "characters": characters,
    }
    doc["users"].append(user)
    _save_users_doc(doc)
    return user


def authenticate(user_id: str, password: str) -> dict | None:
    user = find_user(user_id)
    if user and _verify_password(password, user.get("password_hash", "")):
        return user
    return None


def _get_session_secret() -> bytes:
    if SESSION_SECRET_FILE.exists():
        try:
            secret = bytes.fromhex(SESSION_SECRET_FILE.read_text(encoding="utf-8").strip())
            if secret:
                return secret
        except Exception:
            pass
    secret = secrets.token_bytes(32)
    SESSION_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_SECRET_FILE.write_text(secret.hex(), encoding="utf-8")
    try:
        SESSION_SECRET_FILE.chmod(0o600)
    except OSError:
        pass
    return secret


def make_session_token(user_id: str) -> str:
    expires = int(time.time()) + SESSION_MAX_AGE
    payload = f"{user_id}:{expires}"
    sig = hmac.new(_get_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: str) -> str | None:
    """Return the user id if the token is well-formed, unexpired, and correctly
    signed — otherwise None."""
    try:
        user_id, expires_str, sig = token.split(":", 2)
        payload = f"{user_id}:{expires_str}"
        expected = hmac.new(_get_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expires_str) < int(time.time()):
            return None
        return user_id
    except Exception:
        return None


def get_current_user(request: Request) -> dict:
    """FastAPI dependency: the authenticated user, or 401."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = verify_session_token(token) if token else None
    user = find_user(user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def user_public(user: dict) -> dict:
    return {"id": user["id"], "name": user.get("name") or user["id"], "color": user.get("color") or DEFAULT_USER_COLOR}


def _user_allowed_characters(user: dict) -> str | list[str]:
    return user.get("characters", "all")


def _user_can_access(user: dict, character_id: str) -> bool:
    allowed = _user_allowed_characters(user)
    return allowed == "all" or character_id in allowed


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
    if not CHARACTERS_DIR.is_dir():
        return []
    chars = []
    for d in sorted(CHARACTERS_DIR.iterdir()):
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
            "color": cfg.get("color") or DEFAULT_CHARACTER_COLOR,
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
    return CHARACTERS_DIR / character_id


# ===================== Album helpers =====================

def _album_dir(character_id: str, person_id: str) -> Path:
    d = char_dir(character_id) / "album" / person_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _compress_image(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > ALBUM_MAX_PX:
        img.thumbnail((ALBUM_MAX_PX, ALBUM_MAX_PX), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=ALBUM_JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def _album_filename(person_id: str, title: str) -> str:
    now = datetime.now(TZ)
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
    while len(photos) > ALBUM_MAX_PHOTOS:
        target = next((p for p in photos if p.name not in locks), None)
        if target is None:
            break
        target.unlink(missing_ok=True)
        photos.remove(target)


# ===================== Voice memo helpers =====================

def _voice_memo_dir(character_id: str, person_id: str) -> Path:
    d = char_dir(character_id) / "voice_memo" / person_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _voice_memo_filename(person_id: str, title: str, ext: str = ".wav") -> str:
    now = datetime.now(TZ)
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
        [f for f in d.iterdir() if f.suffix in _AUDIO_EXTS],
        key=lambda f: f.stat().st_mtime,
    )
    while len(memos) > VOICE_MEMO_MAX:
        target = next((f for f in memos if f.name not in locks), None)
        if target is None:
            break
        target.unlink(missing_ok=True)
        memos.remove(target)


# ===================== Mailbox helpers =====================
# Mailbox is already N×N by filename convention (from_<sender>_to_<recipient>_
# <timestamp>.md) — no change needed for Phase A.

def _load_mailbox_meta() -> dict:
    if MAILBOX_METADATA_FILE.exists():
        try:
            return json.loads(MAILBOX_METADATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": 1, "mails": {}}


def _save_mailbox_meta(data: dict):
    MAILBOX_METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAILBOX_METADATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_mail_flags(filename: str) -> dict:
    meta = _load_mailbox_meta()
    return meta.get("mails", {}).get(filename, {"archived": False, "starred": False, "read_by": []})


def _parse_mail_filename(name: str) -> tuple[str, str, str]:
    """Parse from_<sender>_to_<recipient>_YYYYMMDD_HHMM.md → (sender, recipient, date_str)"""
    stem = name.removesuffix(".md")
    parts = stem.split("_")
    if parts[0] == "from" and "to" in parts:
        ti = parts.index("to")
        sender = "_".join(parts[1:ti])
        rest = parts[ti + 1:]
        date_idx = next((i for i, p in enumerate(rest) if len(p) == 8 and p.isdigit()), -1)
        if date_idx > 0:
            recipient = "_".join(rest[:date_idx])
            d, t = rest[date_idx], rest[date_idx + 1] if date_idx + 1 < len(rest) else "0000"
            date_str = f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}"
            return sender, recipient, date_str
    return "", "", ""


# ===================== M5 watcher =====================

# Physical M5 button presses (camera/sensor/mic) aren't tied to any logged-in
# web session, so there's no specific user to name in those prompts (unlike
# chat, which always has one — see call_claude()'s `user` argument).
_M5_EVENT_ACTOR = "そばにいる人"


def _extract_reply_and_session(raw_jsonl: str) -> tuple[str, str]:
    """Extract the assistant's final reply text and the session id from a
    `claude --output-format stream-json` transcript."""
    parts = []
    session_id = ""
    for line in raw_jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    parts.append(block["text"])
        elif ev.get("type") == "result":
            session_id = ev.get("session_id", "") or session_id
    return "\n".join(parts).strip(), session_id


def _session_file(character_id: str, session_key: str) -> Path:
    """Claude CLI `--resume` session id, kept per character×user (Phase B) —
    physical M5 button presses use the shared `_m5` key since they have no
    logged-in user attached."""
    return char_dir(character_id) / "state" / f".session-id.{session_key}"


# ===================== Character lock (Phase C) =====================
# One asyncio.Lock per character id. Every call_claude() invocation for a
# given character — chat, group chat, M5 button reactions, diary generation —
# acquires this lock first, so the character never runs two conversations at
# once ("1 character = 1 mind"). Requests for *other* characters are
# completely unaffected (separate lock per id).
#
# A waiter gives up waiting after CHAR_LOCK_TIMEOUT seconds and force-opens a
# fresh lock rather than starving forever behind a wedged call — call_claude()
# already bounds its own subprocess to 120s, so this is defense in depth for
# anything that could hang before/after the subprocess (file I/O, a future
# code path, etc).

_char_locks: dict[str, asyncio.Lock] = {}
_char_lock_holders: dict[str, dict] = {}  # character_id -> {"user_id", "name", "since"}


def _get_char_lock(character_id: str) -> asyncio.Lock:
    lock = _char_locks.get(character_id)
    if lock is None:
        lock = asyncio.Lock()
        _char_locks[character_id] = lock
    return lock


def character_lock_status(character_id: str) -> dict | None:
    """Current holder of a character's lock ({"user_id", "name", "since"}),
    or None if nobody is talking to it right now."""
    return _char_lock_holders.get(character_id)


@asynccontextmanager
async def _character_lock(character_id: str, holder_id: str, holder_name: str):
    lock = _get_char_lock(character_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=CHAR_LOCK_TIMEOUT)
    except asyncio.TimeoutError:
        # Force-release: swap in a brand new lock for this character and take
        # it uncontended. The old holder (if it ever finishes) will release a
        # lock object nobody else references anymore — harmless.
        lock = asyncio.Lock()
        _char_locks[character_id] = lock
        await lock.acquire()
    since = time.time()
    _char_lock_holders[character_id] = {"user_id": holder_id, "name": holder_name, "since": since}
    try:
        yield
    finally:
        current = _char_lock_holders.get(character_id)
        if current is not None and current.get("since") == since:
            _char_lock_holders.pop(character_id, None)
        lock.release()


async def call_claude(character: dict, message: str, user: dict | None = None, source: str = "chat") -> str:
    """Call Claude CLI for the given character. Saves a stream log under the
    character's stream_logs/ dir for the 記録 tab.

    `user` is the users.json record of whoever is talking (None for M5
    hardware-triggered calls). The conversation continues across calls via
    `claude --resume`, using a session id file scoped to (character, user) —
    "talking with Alice" and "talking with Bob" are different continuous
    contexts for the same character, same as `chat_histories/<user_id>.json`.
    """
    char_id = character["id"]
    char_name = character.get("name") or char_id
    data_dir = Path(character["data_dir"])
    mcp_config = data_dir / "config" / "autonomous-mcp.json"
    if not mcp_config.exists():
        mcp_config = PROJECT_DIR / "autonomous-mcp.json"

    soul_file = data_dir / "SOUL.md"
    soul = soul_file.read_text(encoding="utf-8") if soul_file.exists() else f"あなたは{char_name}です。"

    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M (JST)")
    partner_name = (user.get("name") or user.get("id")) if user else None
    system_prompt = (
        f"{soul}\n\n"
        f"現在の日時: {now_str}\n"
        f"データディレクトリ: {data_dir}/\n"
        f"メールボックス: {MAILBOX_DIR}/\n"
        f"メール送信: `python3 {SCRIPTS_DIR}/write_mailbox.py {char_id} <宛先ユーザーID> '<内容>'`\n"
        + (f"今話しかけているのは{partner_name}です。\n" if partner_name else "")
    )

    session_key = user["id"] if user else "_m5"
    sf = _session_file(char_id, session_key)

    def _build_cmd(resume: bool) -> list[str]:
        cmd = [CLAUDE_CLI_PATH, "--print", "--system-prompt", system_prompt, "--output-format", "stream-json", "--verbose"]
        if mcp_config.exists():
            cmd += ["--mcp-config", str(mcp_config)]
        if resume and sf.exists():
            sid = sf.read_text(encoding="utf-8").strip()
            if sid:
                cmd += ["--resume", sid]
        return cmd

    async def _run(cmd: list[str]) -> str:
        proc = await asyncio.create_subprocess_exec(
            *cmd, message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        return stdout.decode()

    # "1 character = 1 mind": serialize every call for this character behind
    # its lock, whether it's a logged-in human's chat, a group-chat turn, an
    # M5 button reaction, or diary generation.
    holder_id = user["id"] if user else f"_{source}"
    holder_name = (user.get("name") or user["id"]) if user else f"[{source}]"
    async with _character_lock(char_id, holder_id, holder_name):
        try:
            raw = await _run(_build_cmd(resume=True))
            reply, new_sid = _extract_reply_and_session(raw)
            if not reply and sf.exists():
                # The resumed session may have gone stale (pruned by the CLI,
                # corrupted, etc.) — drop it and retry fresh once.
                sf.unlink(missing_ok=True)
                raw = await _run(_build_cmd(resume=False))
                reply, new_sid = _extract_reply_and_session(raw)
            stream_dir = _stream_log_dir(char_id)
            stream_dir.mkdir(parents=True, exist_ok=True)
            stream_path = stream_dir / f"{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}_{source}.jsonl"
            stream_path.write_text(raw, encoding="utf-8")
            if new_sid:
                sf.parent.mkdir(parents=True, exist_ok=True)
                sf.write_text(new_sid, encoding="utf-8")
            return reply
        except Exception as e:
            print(f"[call_claude:{char_id}] error: {e}")
            return ""


async def _m5_camera_react(character: dict, host: str):
    import tempfile
    import urllib.request
    char_id = character["id"]
    try:
        with urllib.request.urlopen(f"http://{host}/snapshot", timeout=10) as r:
            jpeg = r.read()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(jpeg)
            tmp = f.name
        msg = (
            f"{_M5_EVENT_ACTOR}がカメラボタンを押した。写真は {tmp} にある。"
            f"写真を見て感想を `speak` で声に出し、`show_face` か `play_sound` で気持ちを表現して。"
            f"印象に残ったことは `remember` で記憶しておいて。"
            f"誰かに伝えたければ `python3 {SCRIPTS_DIR}/write_mailbox.py {char_id} <宛先ユーザーID> '<感想>'` でメールを送って。"
        )
        await call_claude(character, msg, source="camera")
        Path(tmp).unlink(missing_ok=True)
    except Exception as e:
        print(f"[m5_watcher:{char_id}] camera error: {e}")


async def _m5_sensor_react(character: dict, host: str):
    import urllib.request
    char_id = character["id"]
    try:
        with urllib.request.urlopen(f"http://{host}/sensors", timeout=5) as r:
            sensors = json.loads(r.read())
        msg = (
            f"{_M5_EVENT_ACTOR}がセンサーボタンを押した。センサーデータ: {json.dumps(sensors, ensure_ascii=False)}\n"
            f"今の環境をどう感じるか `speak` で声に出し、`show_face` か `play_sound` で気持ちを表現して。"
            f"気づいたことは `remember` で記憶しておいて。"
        )
        await call_claude(character, msg, source="sensor")
    except Exception as e:
        print(f"[m5_watcher:{char_id}] sensor error: {e}")


async def _m5_mic_react(character: dict, host: str, pcm_bytes: bytes):
    import wave

    import requests as _req
    char_id = character["id"]
    if len(pcm_bytes) < 512 or not _ASR_URL:
        return
    try:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm_bytes)
        buf.seek(0)
        r = await asyncio.to_thread(
            lambda: _req.post(
                f"{_ASR_URL}/transcribe",
                files={"file": ("mic.wav", buf, "audio/wav")},
                timeout=60,
            )
        )
        r.raise_for_status()
        text = r.json().get("text", "").strip()
        if not text:
            return
        print(f"[m5_watcher:{char_id}] transcribed: {text}")
        msg = (
            f"{_M5_EVENT_ACTOR}がマイクボタンを押して話しかけた: 「{text}」\n"
            f"`speak` で返事をして。必要なら `show_face` や `play_sound` も使って。"
        )
        await call_claude(character, msg, source="mic")
    except Exception as e:
        print(f"[m5_watcher:{char_id}] mic error: {e}")


async def _m5_watcher(character: dict):
    """Connect to a character's M5 WebSocket and handle camera/sensor/mic events."""
    char_id = character["id"]
    while True:
        hosts = character.get("m5_hosts") or []
        if not hosts:
            await asyncio.sleep(30)
            continue
        connected = False
        for host in hosts:
            try:
                async with websockets.connect(f"ws://{host}:8080", open_timeout=5) as ws:
                    connected = True
                    print(f"[m5_watcher:{char_id}] connected to {host}")
                    pcm_buffer: list[bytes] = []
                    async for message in ws:
                        if isinstance(message, bytes):
                            pcm_buffer.append(message)
                            continue
                        try:
                            data = json.loads(message)
                        except Exception:
                            continue
                        event = data.get("event")
                        if event == "mic_end":
                            if pcm_buffer:
                                pcm = b"".join(pcm_buffer)
                                pcm_buffer = []
                                asyncio.create_task(_m5_mic_react(character, host, pcm))
                            else:
                                pcm_buffer = []
                        elif event == "menu_select":
                            item = data.get("item")
                            if item == "camera":
                                asyncio.create_task(_m5_camera_react(character, host))
                            elif item == "sensor":
                                asyncio.create_task(_m5_sensor_react(character, host))
                break
            except Exception as e:
                print(f"[m5_watcher:{char_id}] {host} error: {e}")
                continue
        if not connected:
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(1)


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


# ===================== Auth / setup (Phase B) =====================
# Two states, mutually exclusive:
#  - users.json doesn't exist (or is empty) -> only /setup and POST /api/setup
#    work; everything else redirects there (design decision: no hand-editing
#    users.json to get started, same spirit as the M5 firmware's QR
#    provisioning flow).
#  - users.json has at least one user -> /setup 302s to /login; every other
#    path requires a valid session cookie, via this middleware.

_PUBLIC_PATHS = {"/login", "/setup"}
_PUBLIC_API_PATHS = {"/api/auth/login", "/api/setup"}


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path
    is_api = path.startswith("/api/")

    if not users_exist():
        if path == "/setup" or path == "/api/setup":
            return await call_next(request)
        if is_api:
            return JSONResponse({"error": "setup required", "setup_required": True}, status_code=503)
        return RedirectResponse("/setup")

    if path in _PUBLIC_PATHS or path in _PUBLIC_API_PATHS or path.startswith("/api/auth/"):
        # users.json already has someone in it — /setup no longer applies.
        if path == "/setup" or path == "/api/setup":
            if is_api:
                return JSONResponse({"error": "already set up"}, status_code=409)
            return RedirectResponse("/login")
        return await call_next(request)

    token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = verify_session_token(token) if token else None
    if not user_id or not find_user(user_id):
        if is_api:
            return JSONResponse({"error": "authentication required"}, status_code=401)
        return RedirectResponse("/login")

    return await call_next(request)


class SetupRequest(BaseModel):
    id: str
    name: str
    password: str
    color: str | None = None


@app.post("/api/setup")
async def api_setup(body: SetupRequest):
    if users_exist():
        return JSONResponse({"error": "already set up"}, status_code=409)
    if not body.id or not body.password:
        return JSONResponse({"error": "id and password are required"}, status_code=400)
    try:
        # The first account created gets every character (admin-equivalent —
        # there's no separate role system, "all characters" is the privilege).
        create_user(body.id, body.name, body.password, color=body.color, characters="all")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"ok": True}


class LoginRequest(BaseModel):
    id: str
    password: str


@app.post("/api/auth/login")
async def api_login(body: LoginRequest):
    user = authenticate(body.id, body.password)
    if user is None:
        return JSONResponse({"error": "invalid id or password"}, status_code=401)
    token = make_session_token(user["id"])
    resp = JSONResponse({"ok": True, "user": user_public(user)})
    resp.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=SESSION_MAX_AGE,
        httponly=True, samesite="lax",
    )
    return resp


@app.post("/api/auth/logout")
async def api_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE_NAME)
    return resp


@app.get("/api/me")
async def api_me(user: dict = Depends(get_current_user)):
    return user_public(user)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    return HTMLResponse(_SETUP_HTML)


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return HTMLResponse(_LOGIN_HTML)


# ===================== Characters API =====================

@app.get("/api/characters")
async def api_characters(user: dict = Depends(get_current_user)):
    return list_characters(user)


# ===================== Album API =====================

@app.get("/api/{character_id}/album/{person_id}")
async def api_album_list(character_id: str, person_id: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
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


@app.get("/api/{character_id}/album/{person_id}/{filename}")
async def api_album_image(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    path = _album_dir(character_id, person_id) / filename
    if not path.exists() or not path.name.endswith(".jpg"):
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(content=path.read_bytes(), media_type="image/jpeg")


@app.post("/api/{character_id}/album/{person_id}/upload")
async def api_album_upload(character_id: str, person_id: str, file: UploadFile = File(...), title: str = Form("photo"), user: dict = Depends(get_current_user)):
    """Upload a photo (e.g. from a smartphone)."""
    require_character(character_id, user)
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


@app.post("/api/{character_id}/album/snapshot")
async def api_album_snapshot(character_id: str, body: AlbumSnapshotBody, user: dict = Depends(get_current_user)):
    """Save a base64-encoded JPEG snapshot (called by MCP server)."""
    require_character(character_id, user)
    raw = base64.b64decode(body.image_b64)
    compressed = _compress_image(raw)
    fname = _album_filename(body.person_id, body.title)
    (_album_dir(character_id, body.person_id) / fname).write_bytes(compressed)
    _prune_album(character_id, body.person_id)
    return {"ok": True, "filename": fname}


@app.post("/api/{character_id}/album/{person_id}/{filename}/read")
async def api_album_mark_read(character_id: str, person_id: str, filename: str, viewer: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    reads = _load_album_reads(character_id, person_id)
    viewers = reads.get(filename, [])
    if viewer not in viewers:
        viewers.append(viewer)
        reads[filename] = viewers
        _save_album_reads(character_id, person_id, reads)
    return {"ok": True, "read_by": viewers}


@app.post("/api/{character_id}/album/{person_id}/{filename}/lock")
async def api_album_toggle_lock(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
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


@app.delete("/api/{character_id}/album/{person_id}/{filename}")
async def api_album_delete(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    path = _album_dir(character_id, person_id) / filename
    if not path.exists() or not path.name.endswith(".jpg"):
        return JSONResponse({"error": "not found"}, status_code=404)
    path.unlink()
    reads = _load_album_reads(character_id, person_id)
    if filename in reads:
        del reads[filename]
        _save_album_reads(character_id, person_id, reads)
    return {"ok": True}


# ===================== Voice memo API =====================

@app.get("/api/{character_id}/voice_memo/{person_id}")
async def api_voice_memo_list(character_id: str, person_id: str, unlistened_by: str = "", user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    d = _voice_memo_dir(character_id, person_id)
    listens = _load_listens(character_id, person_id)
    locks = _load_voice_locks(character_id, person_id)
    memos = []
    for f in sorted(
        [x for x in d.iterdir() if x.suffix in _AUDIO_EXTS],
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


@app.get("/api/{character_id}/voice_memo/{person_id}/{filename}")
async def api_voice_memo_file(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    path = _voice_memo_dir(character_id, person_id) / filename
    if not path.exists() or path.suffix not in _AUDIO_EXTS:
        return JSONResponse({"error": "not found"}, status_code=404)
    media_types = {".webm": "audio/webm", ".wav": "audio/wav", ".ogg": "audio/ogg",
                   ".mp4": "audio/mp4", ".m4a": "audio/mp4"}
    return Response(content=path.read_bytes(), media_type=media_types.get(path.suffix, "audio/octet-stream"))


@app.post("/api/{character_id}/voice_memo/{person_id}/upload")
async def api_voice_memo_upload(character_id: str, person_id: str, file: UploadFile = File(...), title: str = Form("memo"), user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    data = await file.read()
    if len(data) > VOICE_MEMO_MAX_BYTES:
        return JSONResponse({"error": "file too large"}, status_code=400)
    orig_ext = Path(file.filename or "").suffix.lower() if file.filename else ""
    ext = orig_ext if orig_ext in _AUDIO_EXTS else ".wav"
    fname = _voice_memo_filename(person_id, title, ext)
    (_voice_memo_dir(character_id, person_id) / fname).write_bytes(data)
    _prune_voice_memo(character_id, person_id)
    return {"ok": True, "filename": fname}


@app.post("/api/{character_id}/voice_memo/{person_id}/{filename}/listen")
async def api_voice_memo_mark_listen(character_id: str, person_id: str, filename: str, listener: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    listens = _load_listens(character_id, person_id)
    listeners = listens.get(filename, [])
    if listener not in listeners:
        listeners.append(listener)
        listens[filename] = listeners
        _save_listens(character_id, person_id, listens)
    return {"ok": True, "listened_by": listeners}


@app.post("/api/{character_id}/voice_memo/{person_id}/{filename}/lock")
async def api_voice_memo_toggle_lock(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    path = _voice_memo_dir(character_id, person_id) / filename
    if not path.exists() or path.suffix not in _AUDIO_EXTS:
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


@app.delete("/api/{character_id}/voice_memo/{person_id}/{filename}")
async def api_voice_memo_delete(character_id: str, person_id: str, filename: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    path = _voice_memo_dir(character_id, person_id) / filename
    if not path.exists() or path.suffix not in _AUDIO_EXTS:
        return JSONResponse({"error": "not found"}, status_code=404)
    path.unlink()
    listens = _load_listens(character_id, person_id)
    if filename in listens:
        del listens[filename]
        _save_listens(character_id, person_id, listens)
    return {"ok": True}


# ===================== Notebook API =====================
# Not character-scoped (a family notebook shared across everyone talking to
# any character) — out of Phase A's scope structurally, but the author name
# (Phase B) now always comes from the logged-in user, not free-text input.

class NotebookEntry(BaseModel):
    content: str


@app.get("/api/notebook")
async def api_notebook_list(user: dict = Depends(get_current_user)):
    if not NOTEBOOK_FILE.exists():
        return []
    try:
        entries = json.loads(NOTEBOOK_FILE.read_text(encoding="utf-8"))
        entries.reverse()
        return entries
    except Exception:
        return []


@app.post("/api/notebook")
async def api_notebook_add(entry: NotebookEntry, user: dict = Depends(get_current_user)):
    NOTEBOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if NOTEBOOK_FILE.exists():
        try:
            entries = json.loads(NOTEBOOK_FILE.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    now = datetime.now(TZ).strftime("%Y/%m/%d %H:%M")
    author = user.get("name") or user["id"]
    entries.append({"author": author, "date": now, "content": entry.content})
    NOTEBOOK_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ===================== Mailbox API =====================
# Already N×N via the from_X_to_Y filename convention — no endpoint changes
# needed. The recipient picker in the UI is now populated from /api/characters.

@app.get("/api/mailbox")
def api_mailbox(filter: str = "inbox", offset: int = 0, limit: int = 100):
    """List mail. filter: inbox / archived / starred / all"""
    MAILBOX_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(MAILBOX_DIR.iterdir(), reverse=True):
        if not f.name.endswith(".md"):
            continue
        sender, recipient, date_str = _parse_mail_filename(f.name)
        flags = _get_mail_flags(f.name)
        if filter == "inbox" and flags["archived"]:
            continue
        if filter == "archived" and not flags["archived"]:
            continue
        if filter == "starred" and not flags["starred"]:
            continue
        items.append({
            "filename": f.name,
            "sender": sender,
            "recipient": recipient,
            "date": date_str,
            "content": f.read_text(encoding="utf-8"),
            "archived": flags["archived"],
            "starred": flags["starred"],
            "read_by": flags.get("read_by", []),
        })
    items.sort(key=lambda m: m["date"], reverse=True)
    total = len(items)
    return {"total": total, "offset": offset, "items": items[offset:offset + limit]}


class MailSendRequest(BaseModel):
    from_id: str
    to_id: str
    subject: str = ""
    body: str


@app.post("/api/mail/send")
async def api_mail_send(req: MailSendRequest):
    valid = re.compile(r"^[a-zA-Z0-9_]+$")
    if not valid.match(req.from_id) or not valid.match(req.to_id):
        return JSONResponse({"error": "invalid ID"}, status_code=400)
    content = f"**{req.subject}**\n\n{req.body}".strip() if req.subject else req.body
    proc = await asyncio.create_subprocess_exec(
        "python3", str(SCRIPTS_DIR / "write_mailbox.py"), req.from_id, req.to_id, content,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return JSONResponse({"error": stderr.decode()}, status_code=500)
    return {"ok": True, "file": stdout.decode().strip()}


class MailMetaUpdate(BaseModel):
    archived: bool | None = None
    starred: bool | None = None
    read_by: list[str] | None = None


@app.patch("/api/mailbox/{filename}/meta")
def api_mailbox_update_meta(filename: str, body: MailMetaUpdate):
    safe = Path(filename).name
    if not (MAILBOX_DIR / safe).exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    meta = _load_mailbox_meta()
    flags = meta.setdefault("mails", {}).setdefault(safe, {"archived": False, "starred": False, "read_by": []})
    if body.archived is not None:
        flags["archived"] = body.archived
    if body.starred is not None:
        flags["starred"] = body.starred
    if body.read_by is not None:
        flags["read_by"] = body.read_by
    _save_mailbox_meta(meta)
    return {"ok": True}


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
    log.append({"role": role, "text": text, "timestamp": datetime.now(TZ).isoformat()})
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


@app.post("/api/{character_id}/chat")
async def api_chat(character_id: str, req: ChatRequest, user: dict = Depends(get_current_user)):
    char = require_character(character_id, user)
    _append_chat(character_id, user["id"], user["id"], req.message)
    reply = await call_claude(char, req.message, user=user, source="chat")
    _append_chat(character_id, user["id"], character_id, reply)
    return {"reply": reply}


@app.get("/api/{character_id}/chat/history")
async def api_chat_history(character_id: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    return _load_chat_log(character_id, user["id"])[-100:]


@app.get("/api/{character_id}/chat/status")
async def api_chat_status(character_id: str, user: dict = Depends(get_current_user)):
    """Polled by the UI while a chat request is in flight, so it can show
    '<name>と話し中…' instead of a bare spinner when the character's lock is
    held by someone else (Phase C: one character = one mind)."""
    require_character(character_id, user)
    holder = character_lock_status(character_id)
    if holder and holder["user_id"] != user["id"]:
        return {"busy": True, "partner_name": holder["name"]}
    return {"busy": False, "partner_name": None}


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
    return DATA_DIR / "users" / user_id / "group_chat.json"


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
    now = datetime.now(TZ).isoformat()
    user_name = user.get("name") or user["id"]
    user_color = user.get("color") or DEFAULT_USER_COLOR
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
            "color": char.get("color") or DEFAULT_CHARACTER_COLOR,
            "reply": reply,
        }
        responses.append(r)
        _append_group_log(user["id"], {
            "role": char["id"], "name": r["name"], "color": r["color"],
            "text": reply, "timestamp": datetime.now(TZ).isoformat(),
        })
        yield json.dumps(r, ensure_ascii=False) + "\n"


@app.post("/api/group/chat/stream")
async def api_group_chat_stream(req: GroupChatRequest, user: dict = Depends(get_current_user)):
    chars = _group_characters(user)
    return StreamingResponse(
        _group_chat_stream(chars, req.message, user),
        media_type="application/x-ndjson",
    )


@app.get("/api/group/history")
async def api_group_history(user: dict = Depends(get_current_user)):
    return _load_group_log(user["id"])[-200:]


# ===================== Records API (記録) =====================
# Each call_claude() invocation writes a raw `claude --output-format stream-json`
# transcript under the character's stream_logs/ dir; these endpoints expose
# that for browsing.

def _stream_log_dir(character_id: str) -> Path:
    return char_dir(character_id) / "stream_logs"


def _list_stream_logs(character_id: str) -> list[str]:
    d = _stream_log_dir(character_id)
    if not d.is_dir():
        return []
    files = [f for f in d.glob("*.jsonl") if f.stat().st_size > 0]
    return [f.name for f in sorted(files, reverse=True)][:50]


def _parse_stream_log(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type", "")
        if t == "assistant":
            for block in ev.get("message", {}).get("content", []):
                btype = block.get("type", "")
                if btype == "thinking":
                    out.append({"kind": "thinking", "text": block.get("thinking", "")})
                elif btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        out.append({"kind": "text", "text": text})
                elif btype == "tool_use":
                    out.append({"kind": "tool_call", "name": block.get("name", "?"), "input": block.get("input", {})})
        elif t == "result":
            out.append({"kind": "result", "turns": ev.get("num_turns", 0), "cost_usd": ev.get("total_cost_usd", 0)})
    return out


@app.get("/api/{character_id}/records")
async def api_records_list(character_id: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    return _list_stream_logs(character_id)


@app.get("/api/{character_id}/records/{filename}")
async def api_records_get(character_id: str, filename: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    if not filename.endswith(".jsonl") or "/" in filename or ".." in filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    path = _stream_log_dir(character_id) / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return _parse_stream_log(path)


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


@app.get("/api/{character_id}/diary/{date}")
async def api_diary(character_id: str, date: str, user: dict = Depends(get_current_user)):
    char = require_character(character_id, user)
    today = datetime.now(TZ).strftime("%Y-%m-%d")
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


@app.post("/api/{character_id}/diary/{date}/summarize")
async def api_diary_summarize(character_id: str, date: str, user: dict = Depends(get_current_user)):
    char = require_character(character_id, user)
    entries = _chat_entries_for_date(character_id, date)
    summary = await _generate_diary_summary(char, date, entries)
    if summary:
        d = _diary_dir(character_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{date}.txt").write_text(summary, encoding="utf-8")
    return {"summary": summary}


# ===================== Single-page UI =====================

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_INDEX_HTML)


_INDEX_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>M5 Petit</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: sans-serif; background: #f5f5f5; color: #333; }
header { background: #4a7c59; color: white; padding: 12px 20px; font-size: 18px; font-weight: bold; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
header #header-char-name { font-weight: normal; }
header .header-user { font-size: 12px; font-weight: normal; display: flex; align-items: center; gap: 8px; }
header .header-user .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; }
header .header-user button { background: rgba(255,255,255,.2); padding: 4px 10px; font-size: 12px; }
header .header-user button:hover { background: rgba(255,255,255,.35); }
.char-tabs { display: flex; background: #333; }
.char-tab { padding: 8px 18px; cursor: pointer; border: none; border-bottom: 3px solid transparent; background: none; font-size: 13px; font-weight: bold; color: #ccc; }
.char-tab.active { color: white; }
.tabs { display: flex; background: white; border-bottom: 2px solid #4a7c59; }
.tab { padding: 10px 20px; cursor: pointer; border: none; background: none; font-size: 14px; color: #666; }
.tab.active { color: #4a7c59; font-weight: bold; border-bottom: 2px solid #4a7c59; margin-bottom: -2px; }
.panel { display: none; padding: 16px; max-width: 900px; margin: 0 auto; }
.panel.active { display: block; }
.card { background: white; border-radius: 8px; padding: 12px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
button { background: #4a7c59; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 13px; }
button:hover { background: #3a6449; }
button.danger { background: #c0392b; }
input, textarea, select { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; margin-top: 4px; }
textarea { height: 80px; resize: vertical; }
label { font-size: 13px; color: #555; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.meta { font-size: 11px; color: #999; }
img.thumb { max-width: 200px; max-height: 150px; object-fit: cover; border-radius: 4px; cursor: pointer; }
audio { width: 100%; margin-top: 6px; }
.badge { background: #e74c3c; color: white; border-radius: 10px; font-size: 11px; padding: 2px 6px; }
h3 { font-size: 14px; margin-bottom: 8px; color: #444; }
hr { border: none; border-top: 1px solid #eee; margin: 10px 0; }
</style>
</head>
<body>
<header>
  <span>M5 Petit<span id="header-char-name"></span></span>
  <span class="header-user" id="header-user"></span>
</header>
<div class="char-tabs" id="char-tabs" style="display:none"></div>
<div class="tabs">
  <button class="tab active" onclick="showTab('album')">📷 アルバム</button>
  <button class="tab" onclick="showTab('voice')">🎙️ ボイスメモ</button>
  <button class="tab" onclick="showTab('notebook')">📔 ノート</button>
  <button class="tab" onclick="showTab('mail')">✉️ メール</button>
  <button class="tab" onclick="showTab('chat')">💬 会話</button>
  <button class="tab" id="tab-btn-group" style="display:none" onclick="showTab('group')">👨‍👩‍👧‍👦 グループ会話</button>
  <button class="tab" onclick="showTab('records')">📜 記録</button>
  <button class="tab" onclick="showTab('diary')">📖 日記</button>
</div>

<!-- Album -->
<div id="tab-album" class="panel active">
  <div class="card">
    <h3>写真を送る</h3>
    <label>送信者ID <input id="al-from" style="width:150px"></label>
    <label style="margin-left:8px">タイトル <input id="al-title" value="photo" style="width:150px"></label>
    <label style="margin-left:8px">ファイル <input type="file" id="al-file" accept="image/*" style="width:auto"></label>
    <button onclick="uploadPhoto()" style="margin-left:8px;margin-top:8px">送る</button>
  </div>
  <div class="card">
    <h3>表示するアルバム</h3>
    <label>ID <input id="al-view-id" style="width:150px"></label>
    <button onclick="loadAlbum()" style="margin-left:8px">表示</button>
  </div>
  <div id="album-list"></div>
</div>

<!-- Voice memo -->
<div id="tab-voice" class="panel">
  <div class="card">
    <h3>ボイスメモを送る</h3>
    <label>送信者ID <input id="vm-from" style="width:150px"></label>
    <label style="margin-left:8px">タイトル <input id="vm-title" value="memo" style="width:150px"></label>
    <label style="margin-left:8px">ファイル <input type="file" id="vm-file" accept="audio/*" style="width:auto"></label>
    <button onclick="uploadVoice()" style="margin-left:8px;margin-top:8px">送る</button>
  </div>
  <div class="card">
    <h3>表示するボイスメモ</h3>
    <label>ID <input id="vm-view-id" style="width:150px"></label>
    <button onclick="loadVoiceMemos()" style="margin-left:8px">表示</button>
  </div>
  <div id="voice-list"></div>
</div>

<!-- Notebook -->
<div id="tab-notebook" class="panel">
  <div class="card">
    <h3>書き込む</h3>
    <div class="meta">書き込む人: <span id="nb-author-display">-</span></div>
    <label style="margin-top:6px">内容 <textarea id="nb-content" placeholder="今日の出来事..."></textarea></label>
    <button onclick="addNote()" style="margin-top:8px">追加</button>
  </div>
  <button onclick="loadNotebook()" style="margin-bottom:12px">更新</button>
  <div id="notebook-list"></div>
</div>

<!-- Mail -->
<div id="tab-mail" class="panel">
  <div class="card">
    <h3>メールを送る</h3>
    <div class="row">
      <label style="flex:0 0 auto">From <input id="ml-from" style="width:120px"></label>
      <label style="flex:0 0 auto">To <input id="ml-to" list="char-datalist" style="width:120px"></label>
      <datalist id="char-datalist"></datalist>
    </div>
    <label style="margin-top:6px">件名 <input id="ml-subject" placeholder="件名（省略可）"></label>
    <label style="margin-top:6px">本文 <textarea id="ml-body" placeholder="本文..."></textarea></label>
    <button onclick="sendMail()" style="margin-top:8px">送信</button>
  </div>
  <div class="row" style="margin-bottom:12px;gap:4px">
    <button onclick="loadMail('inbox')">受信トレイ</button>
    <button onclick="loadMail('starred')">スター</button>
    <button onclick="loadMail('archived')">アーカイブ</button>
    <button onclick="loadMail('all')">すべて</button>
  </div>
  <div id="mail-list"></div>
</div>

<!-- Chat -->
<div id="tab-chat" class="panel">
  <div class="card">
    <div id="chat-list" style="max-height:400px;overflow-y:auto"></div>
    <div id="chat-status" class="meta" style="margin-top:4px"></div>
    <div class="row" style="margin-top:8px;align-items:flex-start">
      <textarea id="chat-input" placeholder="メッセージを入力..." style="flex:1"></textarea>
      <button id="chat-send-btn" onclick="sendChat()">送信</button>
    </div>
  </div>
</div>

<!-- Group chat (Phase C: every visible in_group character, replies streamed
     back one at a time as they arrive) -->
<div id="tab-group" class="panel">
  <div class="card">
    <p class="meta" style="margin-bottom:8px">見えているみんなに一度に話しかけます。返事は届いた子から順に表示されます。</p>
    <div id="group-chat-list" style="max-height:400px;overflow-y:auto"></div>
    <div class="row" style="margin-top:8px;align-items:flex-start">
      <textarea id="group-chat-input" placeholder="メッセージを入力..." style="flex:1"></textarea>
      <button id="group-send-btn" onclick="sendGroupChat()">送信</button>
    </div>
  </div>
</div>

<!-- Records -->
<div id="tab-records" class="panel">
  <div class="card">
    <h3>記録一覧</h3>
    <button onclick="loadRecordsList()" style="margin-bottom:8px">更新</button>
    <div id="records-files"></div>
  </div>
  <div id="records-detail"></div>
</div>

<!-- Diary -->
<div id="tab-diary" class="panel">
  <div class="card">
    <div class="row">
      <label>日付 <input type="date" id="diary-date"></label>
      <button onclick="loadDiary()">見る</button>
      <button onclick="summarizeDiary()">日記を書く</button>
    </div>
    <div id="diary-content" class="meta" style="margin-top:8px;white-space:pre-wrap;font-size:14px;color:#333"></div>
  </div>
</div>

<script>
let ME = null;
let CHARACTERS = [];
let currentCharacterId = null;

// ===== Login session (Phase B) =====

async function loadMe() {
  const r = await fetch('/api/me');
  if (r.status === 401) { window.location.href = '/login'; return false; }
  ME = await r.json();
  const el = document.getElementById('header-user');
  el.innerHTML = `<span class="dot" style="background:${ME.color}"></span>${ME.name}<button onclick="logout()">ログアウト / Logout</button>`;
  document.getElementById('al-from').value = ME.id;
  document.getElementById('vm-from').value = ME.id;
  document.getElementById('ml-from').value = ME.id;
  document.getElementById('nb-author-display').textContent = ME.name;
  return true;
}

async function logout() {
  await fetch('/api/auth/logout', {method: 'POST'});
  window.location.href = '/login';
}

// ===== Character selection (Phase A: N characters; Phase B: only the
// characters this logged-in user is allowed to see) =====

async function loadCharacters() {
  if (!(await loadMe())) return;
  CHARACTERS = await fetch('/api/characters').then(r => r.json());
  if (!CHARACTERS.length) {
    document.getElementById('header-char-name').textContent = ' — (キャラクターが見つかりません)';
    return;
  }
  currentCharacterId = CHARACTERS[0].id;
  renderCharTabs();
  onCharacterChanged();
  // Group chat tab only makes sense with 2+ characters this user can see
  // that opted into it (config.json's in_group, default true).
  const groupChars = CHARACTERS.filter(c => c.in_group !== false);
  document.getElementById('tab-btn-group').style.display = groupChars.length >= 2 ? '' : 'none';
}

function renderCharTabs() {
  const el = document.getElementById('char-tabs');
  if (CHARACTERS.length <= 1) { el.style.display = 'none'; updateHeader(); return; }
  el.style.display = 'flex';
  el.innerHTML = CHARACTERS.map(c => `
    <button class="char-tab ${c.id === currentCharacterId ? 'active' : ''}" style="border-bottom-color:${c.color}" onclick="selectCharacter('${c.id}')">${c.name}</button>
  `).join('');
  updateHeader();
}

function selectCharacter(id) {
  currentCharacterId = id;
  renderCharTabs();
  onCharacterChanged();
}

function updateHeader() {
  const c = CHARACTERS.find(c => c.id === currentCharacterId);
  const el = document.getElementById('header-char-name');
  el.textContent = c ? ` — ${c.name}` : '';
  el.style.color = c ? c.color : '';
}

function populateMailToOptions() {
  document.getElementById('char-datalist').innerHTML =
    CHARACTERS.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
}

function onCharacterChanged() {
  document.getElementById('al-view-id').value = currentCharacterId;
  document.getElementById('vm-view-id').value = currentCharacterId;
  document.getElementById('ml-to').value = currentCharacterId;
  populateMailToOptions();
  const active = document.querySelector('.panel.active');
  if (active) refreshActiveTab(active.id.replace('tab-', ''));
}

function refreshActiveTab(name) {
  if (name === 'album') loadAlbum();
  if (name === 'voice') loadVoiceMemos();
  if (name === 'chat') loadChat();
  if (name === 'group') loadGroupChat();
  if (name === 'records') loadRecordsList();
  if (name === 'diary') loadDiary();
}

function showTab(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.currentTarget.classList.add('active');
  if (name === 'notebook') loadNotebook();
  if (name === 'mail') loadMail('inbox');
  if (name === 'diary') document.getElementById('diary-date').value = new Date().toISOString().slice(0, 10);
  refreshActiveTab(name);
}

// Album
async function uploadPhoto() {
  const file = document.getElementById('al-file').files[0];
  if (!file) return alert('ファイルを選んでください');
  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', document.getElementById('al-title').value);
  const pid = document.getElementById('al-from').value;
  const r = await fetch(`/api/${currentCharacterId}/album/${pid}/upload`, {method:'POST', body:fd});
  const j = await r.json();
  if (j.ok) { alert('送った！'); loadAlbum(); }
  else alert('エラー: ' + JSON.stringify(j));
}

async function loadAlbum() {
  const pid = document.getElementById('al-view-id').value;
  const photos = await fetch(`/api/${currentCharacterId}/album/${pid}`).then(r => r.json());
  const el = document.getElementById('album-list');
  if (!photos.length) { el.innerHTML = '<p style="color:#999;padding:8px">写真なし</p>'; return; }
  el.innerHTML = photos.map(p => `
    <div class="card">
      <div class="row">
        <img class="thumb" src="/api/${currentCharacterId}/album/${pid}/${p.filename}" onclick="window.open(this.src)">
        <div style="flex:1">
          <div style="font-weight:bold;font-size:13px">${p.filename}</div>
          <div class="meta">${new Date(p.mtime*1000).toLocaleString('ja-JP')} · ${Math.round(p.size/1024)}KB</div>
          <div class="meta">既読: ${p.read_by.join(', ') || 'なし'} · ${p.locked ? '🔒' : ''}</div>
          <div class="row" style="margin-top:6px;gap:4px">
            <button onclick="lockPhoto('${pid}','${p.filename}')">${p.locked ? '解錠' : '施錠'}</button>
            <button class="danger" onclick="deletePhoto('${pid}','${p.filename}')">削除</button>
          </div>
        </div>
      </div>
    </div>`).join('');
}

async function lockPhoto(pid, fn) {
  await fetch(`/api/${currentCharacterId}/album/${pid}/${fn}/lock`, {method:'POST'});
  loadAlbum();
}
async function deletePhoto(pid, fn) {
  if (!confirm('削除する？')) return;
  await fetch(`/api/${currentCharacterId}/album/${pid}/${fn}`, {method:'DELETE'});
  loadAlbum();
}

// Voice memo
async function uploadVoice() {
  const file = document.getElementById('vm-file').files[0];
  if (!file) return alert('ファイルを選んでください');
  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', document.getElementById('vm-title').value);
  const pid = document.getElementById('vm-from').value;
  const r = await fetch(`/api/${currentCharacterId}/voice_memo/${pid}/upload`, {method:'POST', body:fd});
  const j = await r.json();
  if (j.ok) { alert('送った！'); loadVoiceMemos(); }
  else alert('エラー: ' + JSON.stringify(j));
}

async function loadVoiceMemos() {
  const pid = document.getElementById('vm-view-id').value;
  const memos = await fetch(`/api/${currentCharacterId}/voice_memo/${pid}`).then(r => r.json());
  const el = document.getElementById('voice-list');
  if (!memos.length) { el.innerHTML = '<p style="color:#999;padding:8px">ボイスメモなし</p>'; return; }
  el.innerHTML = memos.map(m => `
    <div class="card">
      <div style="font-weight:bold;font-size:13px">${m.filename}</div>
      <div class="meta">${new Date(m.mtime*1000).toLocaleString('ja-JP')} · ${Math.round(m.size/1024)}KB</div>
      <div class="meta">聴いた: ${m.listened_by.join(', ') || 'なし'} · ${m.locked ? '🔒' : ''}</div>
      <audio controls src="/api/${currentCharacterId}/voice_memo/${pid}/${m.filename}"></audio>
      <div class="row" style="margin-top:6px;gap:4px">
        <button onclick="lockVoice('${pid}','${m.filename}')">${m.locked ? '解錠' : '施錠'}</button>
        <button class="danger" onclick="deleteVoice('${pid}','${m.filename}')">削除</button>
      </div>
    </div>`).join('');
}

async function lockVoice(pid, fn) {
  await fetch(`/api/${currentCharacterId}/voice_memo/${pid}/${fn}/lock`, {method:'POST'});
  loadVoiceMemos();
}
async function deleteVoice(pid, fn) {
  if (!confirm('削除する？')) return;
  await fetch(`/api/${currentCharacterId}/voice_memo/${pid}/${fn}`, {method:'DELETE'});
  loadVoiceMemos();
}

// Notebook (shared, not character-scoped; author always comes from the
// logged-in session server-side, see /api/notebook)
async function addNote() {
  const r = await fetch('/api/notebook', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      content: document.getElementById('nb-content').value,
    })
  });
  document.getElementById('nb-content').value = '';
  loadNotebook();
}

async function loadNotebook() {
  const entries = await fetch('/api/notebook').then(r => r.json());
  const el = document.getElementById('notebook-list');
  if (!entries.length) { el.innerHTML = '<p style="color:#999;padding:8px">まだ何も書いてない</p>'; return; }
  el.innerHTML = entries.map(e => `
    <div class="card">
      <div class="row"><strong>${e.author}</strong><span class="meta" style="margin-left:8px">${e.date}</span></div>
      <div style="margin-top:6px;white-space:pre-wrap;font-size:13px">${e.content}</div>
    </div>`).join('');
}

// Mail (shared, not character-scoped — recipient list comes from CHARACTERS)
async function sendMail() {
  const r = await fetch('/api/mail/send', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      from_id: document.getElementById('ml-from').value,
      to_id: document.getElementById('ml-to').value,
      subject: document.getElementById('ml-subject').value,
      body: document.getElementById('ml-body').value,
    })
  });
  const j = await r.json();
  if (j.ok) { alert('送信した！'); document.getElementById('ml-body').value = ''; loadMail('inbox'); }
  else alert('エラー: ' + JSON.stringify(j));
}

async function loadMail(filter = 'inbox') {
  const data = await fetch(`/api/mailbox?filter=${filter}`).then(r => r.json());
  const el = document.getElementById('mail-list');
  if (!data.items.length) { el.innerHTML = '<p style="color:#999;padding:8px">メールなし</p>'; return; }
  el.innerHTML = data.items.map(m => `
    <div class="card">
      <div class="row">
        <strong>${m.sender} → ${m.recipient}</strong>
        <span class="meta" style="margin-left:8px">${m.date}</span>
        ${m.starred ? '<span class="badge">⭐</span>' : ''}
      </div>
      <div style="margin-top:6px;white-space:pre-wrap;font-size:13px">${m.content}</div>
      <div class="row" style="margin-top:6px;gap:4px">
        <button onclick="toggleStar('${m.filename}', ${!m.starred})">${m.starred ? 'スター解除' : '⭐スター'}</button>
        <button onclick="toggleArchive('${m.filename}', ${!m.archived})">${m.archived ? '受信トレイへ' : 'アーカイブ'}</button>
      </div>
    </div>`).join('');
}

async function toggleStar(fn, starred) {
  await fetch(`/api/mailbox/${fn}/meta`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({starred})
  });
  loadMail('inbox');
}
async function toggleArchive(fn, archived) {
  await fetch(`/api/mailbox/${fn}/meta`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({archived})
  });
  loadMail('inbox');
}

// Chat
async function loadChat() {
  const log = await fetch(`/api/${currentCharacterId}/chat/history`).then(r => r.json());
  const el = document.getElementById('chat-list');
  el.innerHTML = log.map(e => `
    <div class="card">
      <div class="row"><strong>${e.role}</strong><span class="meta" style="margin-left:8px">${new Date(e.timestamp).toLocaleString('ja-JP')}</span></div>
      <div style="margin-top:4px;white-space:pre-wrap;font-size:13px">${e.text}</div>
    </div>`).join('') || '<p style="color:#999;padding:8px">まだ会話なし</p>';
  el.scrollTop = el.scrollHeight;
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const btn = document.getElementById('chat-send-btn');
  const statusEl = document.getElementById('chat-status');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  btn.disabled = true;
  const charId = currentCharacterId;
  // While the request is in flight, poll the lock status so we can show
  // "<name>と話し中…" if this character is currently serving someone else
  // (Phase C: one character = one mind, requests to the same character are
  // queued behind an asyncio lock).
  const statusTimer = setInterval(async () => {
    try {
      const s = await fetch(`/api/${charId}/chat/status`).then(r => r.json());
      statusEl.textContent = s.busy ? `${s.partner_name}と話し中…` : '考え中…';
    } catch (e) { /* ignore transient poll errors */ }
  }, 800);
  statusEl.textContent = '考え中…';
  try {
    await fetch(`/api/${charId}/chat`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message})
    });
  } finally {
    clearInterval(statusTimer);
    statusEl.textContent = '';
    btn.disabled = false;
  }
  if (charId === currentCharacterId) loadChat();
}

// Group chat (Phase C)
async function loadGroupChat() {
  const log = await fetch('/api/group/history').then(r => r.json());
  const el = document.getElementById('group-chat-list');
  el.innerHTML = log.map(e => `
    <div class="card">
      <div class="row"><strong style="color:${e.color || '#333'}">${e.name}</strong><span class="meta" style="margin-left:8px">${new Date(e.timestamp).toLocaleString('ja-JP')}</span></div>
      <div style="margin-top:4px;white-space:pre-wrap;font-size:13px">${e.text}</div>
    </div>`).join('') || '<p style="color:#999;padding:8px">まだグループ会話なし</p>';
  el.scrollTop = el.scrollHeight;
}

async function sendGroupChat() {
  const input = document.getElementById('group-chat-input');
  const btn = document.getElementById('group-send-btn');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  btn.disabled = true;
  const el = document.getElementById('group-chat-list');
  el.insertAdjacentHTML('beforeend', `<div class="card"><strong>${ME ? ME.name : ''}</strong><div style="margin-top:4px;white-space:pre-wrap;font-size:13px">${message}</div></div>`);
  const thinking = document.createElement('div');
  thinking.className = 'card meta';
  thinking.textContent = 'みんなに聞いてる…';
  el.appendChild(thinking);
  el.scrollTop = el.scrollHeight;
  try {
    const res = await fetch('/api/group/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message})
    });
    if (!res.ok || !res.body) throw new Error('stream failed');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      let idx;
      while ((idx = buf.indexOf('\\n')) >= 0) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        if (!line) continue;
        const r = JSON.parse(line);
        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `<strong style="color:${r.color}">${r.name}</strong><div style="margin-top:4px;white-space:pre-wrap;font-size:13px">${r.reply}</div>`;
        el.insertBefore(card, thinking);
        el.scrollTop = el.scrollHeight;
      }
    }
  } catch (e) {
    thinking.textContent = 'エラーが発生しました';
    return;
  } finally {
    btn.disabled = false;
  }
  thinking.remove();
}

// Records
async function loadRecordsList() {
  const files = await fetch(`/api/${currentCharacterId}/records`).then(r => r.json());
  const el = document.getElementById('records-files');
  el.innerHTML = files.map(f => `<button onclick="loadRecord('${f}')" style="margin:2px 4px 2px 0;font-size:12px">${f}</button>`).join('') || '<p style="color:#999;padding:8px">記録なし</p>';
  document.getElementById('records-detail').innerHTML = '';
}

async function loadRecord(fn) {
  const events = await fetch(`/api/${currentCharacterId}/records/${fn}`).then(r => r.json());
  const el = document.getElementById('records-detail');
  el.innerHTML = events.map(e => {
    if (e.kind === 'text') return `<div class="card">💬 ${e.text}</div>`;
    if (e.kind === 'thinking') return `<div class="card meta">🤔 ${e.text}</div>`;
    if (e.kind === 'tool_call') return `<div class="card meta">🔧 ${e.name}(${JSON.stringify(e.input)})</div>`;
    if (e.kind === 'result') return `<div class="card meta">✅ ${e.turns}ターン · $${e.cost_usd}</div>`;
    return '';
  }).join('');
}

// Diary
async function loadDiary() {
  const date = document.getElementById('diary-date').value;
  const j = await fetch(`/api/${currentCharacterId}/diary/${date}`).then(r => r.json());
  document.getElementById('diary-content').textContent = j.summary || `(${j.count}件の会話。まだ日記なし)`;
}

async function summarizeDiary() {
  const date = document.getElementById('diary-date').value;
  const j = await fetch(`/api/${currentCharacterId}/diary/${date}/summarize`, {method: 'POST'}).then(r => r.json());
  document.getElementById('diary-content').textContent = j.summary || '(会話がありませんでした)';
}

// Load characters, then the default tab, on start
loadCharacters();
</script>
</body>
</html>
"""


# ===================== Login / setup pages (Phase B) =====================
# Plain HTML forms (no framework) — bilingual labels, mobile-friendly single
# column, same visual language as the main app (#4a7c59 green).

_AUTH_PAGE_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: sans-serif; background: #f5f5f5; color: #333; min-height: 100vh;
       display: flex; align-items: center; justify-content: center; padding: 16px; }
.box { background: white; border-radius: 12px; padding: 28px 24px; box-shadow: 0 2px 12px rgba(0,0,0,.1);
       width: 100%; max-width: 360px; }
h1 { font-size: 18px; color: #4a7c59; margin-bottom: 4px; }
h1 .en { display: block; font-size: 12px; font-weight: normal; color: #999; }
p.hint { font-size: 12px; color: #888; margin: 4px 0 16px; }
label { display: block; font-size: 13px; color: #555; margin-top: 14px; }
label .en { color: #999; font-weight: normal; }
input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 15px; margin-top: 4px; }
button { width: 100%; background: #4a7c59; color: white; border: none; padding: 12px; border-radius: 6px;
         cursor: pointer; font-size: 15px; margin-top: 20px; }
button:hover { background: #3a6449; }
#error { color: #c0392b; font-size: 13px; margin-top: 12px; display: none; }
.swap { text-align: center; font-size: 12px; margin-top: 16px; }
.swap a { color: #4a7c59; }
"""

_SETUP_HTML = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>はじめての設定 — M5 Petit</title>
<style>{_AUTH_PAGE_STYLE}</style>
</head>
<body>
<div class="box">
  <h1>はじめての設定<span class="en">First-time setup</span></h1>
  <p class="hint">最初のユーザー（管理者）を作成します。<br>Create the first user account (gets access to every character).</p>
  <label>ユーザーID (半角英数字) <span class="en">User ID (alphanumeric)</span>
    <input id="su-id" autocomplete="username">
  </label>
  <label>表示名 <span class="en">Display name</span>
    <input id="su-name" autocomplete="nickname">
  </label>
  <label>パスワード <span class="en">Password</span>
    <input id="su-password" type="password" autocomplete="new-password">
  </label>
  <label>色 (任意) <span class="en">Color (optional)</span>
    <input id="su-color" type="color" value="#7da8f5" style="padding:2px;height:40px">
  </label>
  <button onclick="doSetup()">作成してログインへ / Create &amp; go to login</button>
  <div id="error"></div>
</div>
<script>
async function doSetup() {{
  const id = document.getElementById('su-id').value.trim();
  const name = document.getElementById('su-name').value.trim();
  const password = document.getElementById('su-password').value;
  const color = document.getElementById('su-color').value;
  const errEl = document.getElementById('error');
  errEl.style.display = 'none';
  const r = await fetch('/api/setup', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{id, name, password, color}})
  }});
  const j = await r.json();
  if (r.ok && j.ok) {{
    window.location.href = '/login';
  }} else {{
    errEl.textContent = j.error || 'エラーが発生しました / Something went wrong';
    errEl.style.display = 'block';
  }}
}}
document.getElementById('su-password').addEventListener('keydown', e => {{ if (e.key === 'Enter') doSetup(); }});
</script>
</body>
</html>
"""

_LOGIN_HTML = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ログイン — M5 Petit</title>
<style>{_AUTH_PAGE_STYLE}</style>
</head>
<body>
<div class="box">
  <h1>ログイン<span class="en">Log in</span></h1>
  <label>ユーザーID <span class="en">User ID</span>
    <input id="li-id" autocomplete="username">
  </label>
  <label>パスワード <span class="en">Password</span>
    <input id="li-password" type="password" autocomplete="current-password">
  </label>
  <button onclick="doLogin()">ログイン / Log in</button>
  <div id="error"></div>
</div>
<script>
async function doLogin() {{
  const id = document.getElementById('li-id').value.trim();
  const password = document.getElementById('li-password').value;
  const errEl = document.getElementById('error');
  errEl.style.display = 'none';
  const r = await fetch('/api/auth/login', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{id, password}})
  }});
  const j = await r.json();
  if (r.ok && j.ok) {{
    window.location.href = '/';
  }} else {{
    errEl.textContent = j.error || 'IDまたはパスワードが違います / Invalid ID or password';
    errEl.style.display = 'block';
  }}
}}
document.getElementById('li-password').addEventListener('keydown', e => {{ if (e.key === 'Enter') doLogin(); }});
</script>
</body>
</html>
"""


# ===================== Entry point =====================

def main():
    import uvicorn
    port = int(os.environ.get("PORT", "8765"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
