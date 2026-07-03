"""M5 Petit App — dashboard server for 1 user / 1 M5 setup.

Environment variables:
  CHARACTER_ID    M5 character ID (default: "petit")
  USER_ID         Human user ID (default: "user")
  M5_HOST         M5 device hostname or IP (comma-separated for fallback)
  M5_HOSTS        Same as M5_HOST (alias)
  VOICE_API_HOST  ASR server host (for mic transcription)
  PETIT_DATA_DIR  Data directory (default: ~/petit_data)
  PROJECT_DIR     Project root for Claude CLI and scripts (default: this file's parent)

Runs on port 8765 by default. Override with PORT env var or uvicorn args.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import websockets
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from PIL import Image, ImageOps
from pydantic import BaseModel

# ===================== Config =====================

CHARACTER_ID = os.environ.get("CHARACTER_ID", "petit")
USER_ID = os.environ.get("USER_ID", "user")
VOICE_API_HOST = os.environ.get("VOICE_API_HOST", "")
_ASR_URL = f"http://{VOICE_API_HOST}:8765" if VOICE_API_HOST else ""

DATA_DIR = Path(os.environ.get("PETIT_DATA_DIR", Path.home() / "petit_data"))
PROJECT_DIR = Path(os.environ.get("PROJECT_DIR", Path(__file__).parent))
SCRIPTS_DIR = PROJECT_DIR / "scripts"

MAILBOX_DIR = DATA_DIR / "mailbox"
ALBUM_DIR = DATA_DIR / "photo_album"
VOICE_MEMO_DIR = DATA_DIR / "voice_memo"
NOTEBOOK_FILE = DATA_DIR / "notebook" / USER_ID / "notebook.json"
MAILBOX_METADATA_FILE = DATA_DIR / "mailbox" / ".metadata.json"

# Per-character data (shared on-disk layout with other M5 Petit tools, e.g. MCP servers)
CHAR_DATA_DIR = DATA_DIR / "characters" / CHARACTER_ID
CHAT_LOG_FILE = CHAR_DATA_DIR / "chat_histories" / "chat_history.json"
STREAM_LOG_DIR = CHAR_DATA_DIR / "stream_logs"
DIARY_DIR = CHAR_DATA_DIR / "diary"

ALBUM_MAX_PHOTOS = 50
ALBUM_MAX_PX = 1200
ALBUM_JPEG_QUALITY = 80

VOICE_MEMO_MAX = 20
VOICE_MEMO_MAX_BYTES = 6 * 1024 * 1024  # ~30 sec

_AUDIO_EXTS = {".webm", ".wav", ".ogg", ".mp4", ".m4a"}

TZ = ZoneInfo("Asia/Tokyo")


def _m5_hosts() -> list[str]:
    raw = os.environ.get("M5_HOSTS", "") or os.environ.get("M5_HOST", "")
    return [h.strip() for h in raw.split(",") if h.strip()]


# ===================== Album helpers =====================

def _album_dir(person_id: str) -> Path:
    d = ALBUM_DIR / person_id
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


def _album_reads_file(person_id: str) -> Path:
    return _album_dir(person_id) / ".reads.json"


def _load_album_reads(person_id: str) -> dict:
    f = _album_reads_file(person_id)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def _save_album_reads(person_id: str, data: dict):
    _album_reads_file(person_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _album_locks_file(person_id: str) -> Path:
    return _album_dir(person_id) / ".locks.json"


def _load_album_locks(person_id: str) -> set:
    f = _album_locks_file(person_id)
    try:
        return set(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else set()
    except Exception:
        return set()


def _save_album_locks(person_id: str, locks: set):
    _album_locks_file(person_id).write_text(json.dumps(list(locks), ensure_ascii=False), encoding="utf-8")


def _prune_album(person_id: str):
    d = _album_dir(person_id)
    locks = _load_album_locks(person_id)
    photos = sorted(d.glob("*.jpg"))
    while len(photos) > ALBUM_MAX_PHOTOS:
        target = next((p for p in photos if p.name not in locks), None)
        if target is None:
            break
        target.unlink(missing_ok=True)
        photos.remove(target)


# ===================== Voice memo helpers =====================

def _voice_memo_dir(person_id: str) -> Path:
    d = VOICE_MEMO_DIR / person_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _voice_memo_filename(person_id: str, title: str, ext: str = ".wav") -> str:
    now = datetime.now(TZ)
    safe = re.sub(r"[^\w-]", "_", title)[:40]
    return f"{now.strftime('%Y%m%d_%H%M%S')}_{person_id}_{safe}{ext}"


def _voice_locks_file(person_id: str) -> Path:
    return _voice_memo_dir(person_id) / ".locks.json"


def _load_voice_locks(person_id: str) -> set:
    f = _voice_locks_file(person_id)
    try:
        return set(json.loads(f.read_text(encoding="utf-8"))) if f.exists() else set()
    except Exception:
        return set()


def _save_voice_locks(person_id: str, locks: set):
    _voice_locks_file(person_id).write_text(json.dumps(list(locks), ensure_ascii=False), encoding="utf-8")


def _listens_file(person_id: str) -> Path:
    return _voice_memo_dir(person_id) / ".listens.json"


def _load_listens(person_id: str) -> dict:
    f = _listens_file(person_id)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:
        return {}


def _save_listens(person_id: str, data: dict):
    _listens_file(person_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _prune_voice_memo(person_id: str):
    d = _voice_memo_dir(person_id)
    locks = _load_voice_locks(person_id)
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

def _extract_reply_text(raw_jsonl: str) -> str:
    """Extract the assistant's final reply text from a `claude --output-format stream-json` transcript."""
    parts = []
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
    return "\n".join(parts).strip()


async def call_claude(message: str, source: str = "chat") -> str:
    """Call Claude CLI for the configured character. Saves a stream log under STREAM_LOG_DIR for the 記録 tab."""
    char_data_dir = CHAR_DATA_DIR
    mcp_config = char_data_dir / "config" / "autonomous-mcp.json"
    if not mcp_config.exists():
        mcp_config = PROJECT_DIR / "autonomous-mcp.json"

    soul_file = char_data_dir / "SOUL.md"
    soul = soul_file.read_text(encoding="utf-8") if soul_file.exists() else f"あなたは{CHARACTER_ID}です。"

    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M (JST)")
    system_prompt = (
        f"{soul}\n\n"
        f"現在の日時: {now_str}\n"
        f"データディレクトリ: {char_data_dir}/\n"
        f"メールボックス: {MAILBOX_DIR}/\n"
        f"メール送信: `python3 {SCRIPTS_DIR}/write_mailbox.py {CHARACTER_ID} {USER_ID} '<内容>'`\n"
    )

    cmd = ["claude", "--print", "--system-prompt", system_prompt, "--output-format", "stream-json", "--verbose"]
    if mcp_config.exists():
        cmd += ["--mcp-config", str(mcp_config)]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        raw = stdout.decode()
        STREAM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        stream_path = STREAM_LOG_DIR / f"{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}_{source}.jsonl"
        stream_path.write_text(raw, encoding="utf-8")
        return _extract_reply_text(raw)
    except Exception as e:
        print(f"[call_claude] error: {e}")
        return ""


async def _m5_camera_react(host: str):
    import urllib.request, tempfile
    try:
        with urllib.request.urlopen(f"http://{host}/snapshot", timeout=10) as r:
            jpeg = r.read()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(jpeg)
            tmp = f.name
        msg = (
            f"{USER_ID}がカメラボタンを押した。写真は {tmp} にある。"
            f"写真を見て感想を `speak` で声に出し、`show_face` か `play_sound` で気持ちを表現して。"
            f"印象に残ったことは `remember` で記憶しておいて。"
            f"終わったら `python3 {SCRIPTS_DIR}/write_mailbox.py {CHARACTER_ID} {USER_ID} '<感想>'` でメールを送って。"
        )
        await call_claude(msg, source="camera")
        Path(tmp).unlink(missing_ok=True)
    except Exception as e:
        print(f"[m5_watcher] camera error: {e}")


async def _m5_sensor_react(host: str):
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}/sensors", timeout=5) as r:
            sensors = json.loads(r.read())
        msg = (
            f"{USER_ID}がセンサーボタンを押した。センサーデータ: {json.dumps(sensors, ensure_ascii=False)}\n"
            f"今の環境をどう感じるか `speak` で声に出し、`show_face` か `play_sound` で気持ちを表現して。"
            f"気づいたことは `remember` で記憶しておいて。"
        )
        await call_claude(msg, source="sensor")
    except Exception as e:
        print(f"[m5_watcher] sensor error: {e}")


async def _m5_mic_react(host: str, pcm_bytes: bytes):
    import wave, tempfile, requests as _req
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
        print(f"[m5_watcher] transcribed: {text}")
        msg = (
            f"{USER_ID}がマイクボタンを押して話しかけた: 「{text}」\n"
            f"`speak` で返事をして。必要なら `show_face` や `play_sound` も使って。"
        )
        await call_claude(msg, source="mic")
    except Exception as e:
        print(f"[m5_watcher] mic error: {e}")


async def _m5_watcher():
    """Connect to M5 WebSocket and handle camera/sensor/mic events."""
    while True:
        hosts = _m5_hosts()
        if not hosts:
            await asyncio.sleep(30)
            continue
        connected = False
        for host in hosts:
            try:
                async with websockets.connect(f"ws://{host}:8080", open_timeout=5) as ws:
                    connected = True
                    print(f"[m5_watcher] connected to {host}")
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
                                asyncio.create_task(_m5_mic_react(host, pcm))
                            else:
                                pcm_buffer = []
                        elif event == "menu_select":
                            item = data.get("item")
                            if item == "camera":
                                asyncio.create_task(_m5_camera_react(host))
                            elif item == "sensor":
                                asyncio.create_task(_m5_sensor_react(host))
                break
            except Exception as e:
                print(f"[m5_watcher] {host} error: {e}")
                continue
        if not connected:
            await asyncio.sleep(30)
        else:
            await asyncio.sleep(1)


# ===================== App lifecycle =====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_m5_watcher())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


# ===================== Album API =====================

@app.get("/api/album/{person_id}")
async def api_album_list(person_id: str):
    d = _album_dir(person_id)
    reads = _load_album_reads(person_id)
    locks = _load_album_locks(person_id)
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


@app.get("/api/album/{person_id}/{filename}")
async def api_album_image(person_id: str, filename: str):
    path = _album_dir(person_id) / filename
    if not path.exists() or not path.name.endswith(".jpg"):
        return JSONResponse({"error": "not found"}, status_code=404)
    return Response(content=path.read_bytes(), media_type="image/jpeg")


@app.post("/api/album/{person_id}/upload")
async def api_album_upload(person_id: str, file: UploadFile = File(...), title: str = Form("photo")):
    """Upload a photo (e.g. from a smartphone)."""
    data = await file.read()
    compressed = _compress_image(data)
    fname = _album_filename(person_id, title)
    (_album_dir(person_id) / fname).write_bytes(compressed)
    _prune_album(person_id)
    return {"ok": True, "filename": fname}


class AlbumSnapshotBody(BaseModel):
    person_id: str
    title: str
    image_b64: str


@app.post("/api/album/snapshot")
async def api_album_snapshot(body: AlbumSnapshotBody):
    """Save a base64-encoded JPEG snapshot (called by MCP server)."""
    raw = base64.b64decode(body.image_b64)
    compressed = _compress_image(raw)
    fname = _album_filename(body.person_id, body.title)
    (_album_dir(body.person_id) / fname).write_bytes(compressed)
    _prune_album(body.person_id)
    return {"ok": True, "filename": fname}


@app.post("/api/album/{person_id}/{filename}/read")
async def api_album_mark_read(person_id: str, filename: str, viewer: str):
    reads = _load_album_reads(person_id)
    viewers = reads.get(filename, [])
    if viewer not in viewers:
        viewers.append(viewer)
        reads[filename] = viewers
        _save_album_reads(person_id, reads)
    return {"ok": True, "read_by": viewers}


@app.post("/api/album/{person_id}/{filename}/lock")
async def api_album_toggle_lock(person_id: str, filename: str):
    path = _album_dir(person_id) / filename
    if not path.exists() or not filename.endswith(".jpg"):
        return JSONResponse({"error": "not found"}, status_code=404)
    locks = _load_album_locks(person_id)
    if filename in locks:
        locks.discard(filename)
        locked = False
    else:
        locks.add(filename)
        locked = True
    _save_album_locks(person_id, locks)
    return {"ok": True, "locked": locked}


@app.delete("/api/album/{person_id}/{filename}")
async def api_album_delete(person_id: str, filename: str):
    path = _album_dir(person_id) / filename
    if not path.exists() or not path.name.endswith(".jpg"):
        return JSONResponse({"error": "not found"}, status_code=404)
    path.unlink()
    reads = _load_album_reads(person_id)
    if filename in reads:
        del reads[filename]
        _save_album_reads(person_id, reads)
    return {"ok": True}


# ===================== Voice memo API =====================

@app.get("/api/voice_memo/{person_id}")
async def api_voice_memo_list(person_id: str, unlistened_by: str = ""):
    d = _voice_memo_dir(person_id)
    listens = _load_listens(person_id)
    locks = _load_voice_locks(person_id)
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


@app.get("/api/voice_memo/{person_id}/{filename}")
async def api_voice_memo_file(person_id: str, filename: str):
    path = _voice_memo_dir(person_id) / filename
    if not path.exists() or path.suffix not in _AUDIO_EXTS:
        return JSONResponse({"error": "not found"}, status_code=404)
    media_types = {".webm": "audio/webm", ".wav": "audio/wav", ".ogg": "audio/ogg",
                   ".mp4": "audio/mp4", ".m4a": "audio/mp4"}
    return Response(content=path.read_bytes(), media_type=media_types.get(path.suffix, "audio/octet-stream"))


@app.post("/api/voice_memo/{person_id}/upload")
async def api_voice_memo_upload(person_id: str, file: UploadFile = File(...), title: str = Form("memo")):
    data = await file.read()
    if len(data) > VOICE_MEMO_MAX_BYTES:
        return JSONResponse({"error": "file too large"}, status_code=400)
    orig_ext = Path(file.filename or "").suffix.lower() if file.filename else ""
    ext = orig_ext if orig_ext in _AUDIO_EXTS else ".wav"
    fname = _voice_memo_filename(person_id, title, ext)
    (_voice_memo_dir(person_id) / fname).write_bytes(data)
    _prune_voice_memo(person_id)
    return {"ok": True, "filename": fname}


@app.post("/api/voice_memo/{person_id}/{filename}/listen")
async def api_voice_memo_mark_listen(person_id: str, filename: str, listener: str):
    listens = _load_listens(person_id)
    listeners = listens.get(filename, [])
    if listener not in listeners:
        listeners.append(listener)
        listens[filename] = listeners
        _save_listens(person_id, listens)
    return {"ok": True, "listened_by": listeners}


@app.post("/api/voice_memo/{person_id}/{filename}/lock")
async def api_voice_memo_toggle_lock(person_id: str, filename: str):
    path = _voice_memo_dir(person_id) / filename
    if not path.exists() or path.suffix not in _AUDIO_EXTS:
        return JSONResponse({"error": "not found"}, status_code=404)
    locks = _load_voice_locks(person_id)
    if filename in locks:
        locks.discard(filename)
        locked = False
    else:
        locks.add(filename)
        locked = True
    _save_voice_locks(person_id, locks)
    return {"ok": True, "locked": locked}


@app.delete("/api/voice_memo/{person_id}/{filename}")
async def api_voice_memo_delete(person_id: str, filename: str):
    path = _voice_memo_dir(person_id) / filename
    if not path.exists() or path.suffix not in _AUDIO_EXTS:
        return JSONResponse({"error": "not found"}, status_code=404)
    path.unlink()
    listens = _load_listens(person_id)
    if filename in listens:
        del listens[filename]
        _save_listens(person_id, listens)
    return {"ok": True}


# ===================== Notebook API =====================

class NotebookEntry(BaseModel):
    author: str
    content: str


@app.get("/api/notebook")
async def api_notebook_list():
    if not NOTEBOOK_FILE.exists():
        return []
    try:
        entries = json.loads(NOTEBOOK_FILE.read_text(encoding="utf-8"))
        entries.reverse()
        return entries
    except Exception:
        return []


@app.post("/api/notebook")
async def api_notebook_add(entry: NotebookEntry):
    NOTEBOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if NOTEBOOK_FILE.exists():
        try:
            entries = json.loads(NOTEBOOK_FILE.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    now = datetime.now(TZ).strftime("%Y/%m/%d %H:%M")
    entries.append({"author": entry.author, "date": now, "content": entry.content})
    NOTEBOOK_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True}


# ===================== Mailbox API =====================

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

def _load_chat_log() -> list[dict]:
    if not CHAT_LOG_FILE.exists():
        return []
    try:
        return json.loads(CHAT_LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_chat_log(log: list[dict]) -> None:
    CHAT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHAT_LOG_FILE.write_text(json.dumps(log[-200:], ensure_ascii=False, indent=2), encoding="utf-8")


def _append_chat(role: str, text: str) -> None:
    log = _load_chat_log()
    log.append({"role": role, "text": text, "timestamp": datetime.now(TZ).isoformat()})
    _save_chat_log(log)


class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    _append_chat(USER_ID, req.message)
    reply = await call_claude(req.message, source="chat")
    _append_chat(CHARACTER_ID, reply)
    return {"reply": reply}


@app.get("/api/chat/history")
async def api_chat_history():
    return _load_chat_log()[-100:]


# ===================== Records API (記録) =====================
# Each call_claude() invocation writes a raw `claude --output-format stream-json`
# transcript under STREAM_LOG_DIR; these endpoints expose that for browsing.

def _list_stream_logs() -> list[str]:
    if not STREAM_LOG_DIR.is_dir():
        return []
    files = [f for f in STREAM_LOG_DIR.glob("*.jsonl") if f.stat().st_size > 0]
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


@app.get("/api/records")
async def api_records_list():
    return _list_stream_logs()


@app.get("/api/records/{filename}")
async def api_records_get(filename: str):
    if not filename.endswith(".jsonl") or "/" in filename or ".." in filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    path = STREAM_LOG_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return _parse_stream_log(path)


# ===================== Diary API (日記) =====================
# Summarized from the day's chat_log entries (no external memory store needed).

def _chat_entries_for_date(date: str) -> list[dict]:
    return [e for e in _load_chat_log() if e["timestamp"].startswith(date)]


async def _generate_diary_summary(date: str, entries: list[dict]) -> str:
    if not entries:
        return ""
    convo = "\n".join(f"{e['role']}: {e['text']}" for e in entries)
    prompt = (
        f"{date} の{USER_ID}と{CHARACTER_ID}の会話ログです。{CHARACTER_ID}の視点で、"
        f"その日の出来事や気持ちを3〜5行の日記としてまとめてください。\n\n{convo}"
    )
    return await call_claude(prompt, source="diary")


@app.get("/api/diary/{date}")
async def api_diary(date: str):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    entries = _chat_entries_for_date(date)
    cache_file = DIARY_DIR / f"{date}.txt"
    if cache_file.exists():
        summary = cache_file.read_text(encoding="utf-8")
    elif date == today:
        # Don't auto-generate today's diary — wait for the manual "書く" button.
        summary = None
    else:
        summary = await _generate_diary_summary(date, entries)
        if summary:
            DIARY_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(summary, encoding="utf-8")
    return {"date": date, "summary": summary, "count": len(entries)}


@app.post("/api/diary/{date}/summarize")
async def api_diary_summarize(date: str):
    entries = _chat_entries_for_date(date)
    summary = await _generate_diary_summary(date, entries)
    if summary:
        DIARY_DIR.mkdir(parents=True, exist_ok=True)
        (DIARY_DIR / f"{date}.txt").write_text(summary, encoding="utf-8")
    return {"summary": summary}


# ===================== Single-page UI =====================

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(_INDEX_HTML)


_INDEX_HTML = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>M5 Petit</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: sans-serif; background: #f5f5f5; color: #333; }}
header {{ background: #4a7c59; color: white; padding: 12px 20px; font-size: 18px; font-weight: bold; }}
.tabs {{ display: flex; background: white; border-bottom: 2px solid #4a7c59; }}
.tab {{ padding: 10px 20px; cursor: pointer; border: none; background: none; font-size: 14px; color: #666; }}
.tab.active {{ color: #4a7c59; font-weight: bold; border-bottom: 2px solid #4a7c59; margin-bottom: -2px; }}
.panel {{ display: none; padding: 16px; max-width: 900px; margin: 0 auto; }}
.panel.active {{ display: block; }}
.card {{ background: white; border-radius: 8px; padding: 12px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
button {{ background: #4a7c59; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 13px; }}
button:hover {{ background: #3a6449; }}
button.danger {{ background: #c0392b; }}
input, textarea, select {{ width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; margin-top: 4px; }}
textarea {{ height: 80px; resize: vertical; }}
label {{ font-size: 13px; color: #555; }}
.row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
.meta {{ font-size: 11px; color: #999; }}
img.thumb {{ max-width: 200px; max-height: 150px; object-fit: cover; border-radius: 4px; cursor: pointer; }}
audio {{ width: 100%; margin-top: 6px; }}
.badge {{ background: #e74c3c; color: white; border-radius: 10px; font-size: 11px; padding: 2px 6px; }}
h3 {{ font-size: 14px; margin-bottom: 8px; color: #444; }}
hr {{ border: none; border-top: 1px solid #eee; margin: 10px 0; }}
</style>
</head>
<body>
<header>M5 Petit — {CHARACTER_ID}</header>
<div class="tabs">
  <button class="tab active" onclick="showTab('album')">📷 アルバム</button>
  <button class="tab" onclick="showTab('voice')">🎙️ ボイスメモ</button>
  <button class="tab" onclick="showTab('notebook')">📔 ノート</button>
  <button class="tab" onclick="showTab('mail')">✉️ メール</button>
  <button class="tab" onclick="showTab('chat')">💬 会話</button>
  <button class="tab" onclick="showTab('records')">📜 記録</button>
  <button class="tab" onclick="showTab('diary')">📖 日記</button>
</div>

<!-- Album -->
<div id="tab-album" class="panel active">
  <div class="card">
    <h3>写真を送る</h3>
    <label>送信者ID <input id="al-from" value="{USER_ID}" style="width:150px"></label>
    <label style="margin-left:8px">タイトル <input id="al-title" value="photo" style="width:150px"></label>
    <label style="margin-left:8px">ファイル <input type="file" id="al-file" accept="image/*" style="width:auto"></label>
    <button onclick="uploadPhoto()" style="margin-left:8px;margin-top:8px">送る</button>
  </div>
  <div class="card">
    <h3>表示するアルバム</h3>
    <label>ID <input id="al-view-id" value="{CHARACTER_ID}" style="width:150px"></label>
    <button onclick="loadAlbum()" style="margin-left:8px">表示</button>
  </div>
  <div id="album-list"></div>
</div>

<!-- Voice memo -->
<div id="tab-voice" class="panel">
  <div class="card">
    <h3>ボイスメモを送る</h3>
    <label>送信者ID <input id="vm-from" value="{USER_ID}" style="width:150px"></label>
    <label style="margin-left:8px">タイトル <input id="vm-title" value="memo" style="width:150px"></label>
    <label style="margin-left:8px">ファイル <input type="file" id="vm-file" accept="audio/*" style="width:auto"></label>
    <button onclick="uploadVoice()" style="margin-left:8px;margin-top:8px">送る</button>
  </div>
  <div class="card">
    <h3>表示するボイスメモ</h3>
    <label>ID <input id="vm-view-id" value="{CHARACTER_ID}" style="width:150px"></label>
    <button onclick="loadVoiceMemos()" style="margin-left:8px">表示</button>
  </div>
  <div id="voice-list"></div>
</div>

<!-- Notebook -->
<div id="tab-notebook" class="panel">
  <div class="card">
    <h3>書き込む</h3>
    <label>著者 <input id="nb-author" value="{USER_ID}" style="width:150px"></label>
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
      <label style="flex:0 0 auto">From <input id="ml-from" value="{USER_ID}" style="width:120px"></label>
      <label style="flex:0 0 auto">To <input id="ml-to" value="{CHARACTER_ID}" style="width:120px"></label>
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
    <div class="row" style="margin-top:8px;align-items:flex-start">
      <textarea id="chat-input" placeholder="メッセージを入力..." style="flex:1"></textarea>
      <button onclick="sendChat()">送信</button>
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
const CHARACTER_ID = "{CHARACTER_ID}";
const USER_ID = "{USER_ID}";

function showTab(name) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.currentTarget.classList.add('active');
  if (name === 'notebook') loadNotebook();
  if (name === 'mail') loadMail('inbox');
  if (name === 'chat') loadChat();
  if (name === 'records') loadRecordsList();
  if (name === 'diary') {{
    document.getElementById('diary-date').value = new Date().toISOString().slice(0, 10);
    loadDiary();
  }}
}}

// Album
async function uploadPhoto() {{
  const file = document.getElementById('al-file').files[0];
  if (!file) return alert('ファイルを選んでください');
  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', document.getElementById('al-title').value);
  const pid = document.getElementById('al-from').value;
  const r = await fetch(`/api/album/${{pid}}/upload`, {{method:'POST', body:fd}});
  const j = await r.json();
  if (j.ok) {{ alert('送った！'); loadAlbum(); }}
  else alert('エラー: ' + JSON.stringify(j));
}}

async function loadAlbum() {{
  const pid = document.getElementById('al-view-id').value;
  const photos = await fetch(`/api/album/${{pid}}`).then(r => r.json());
  const el = document.getElementById('album-list');
  if (!photos.length) {{ el.innerHTML = '<p style="color:#999;padding:8px">写真なし</p>'; return; }}
  el.innerHTML = photos.map(p => `
    <div class="card">
      <div class="row">
        <img class="thumb" src="/api/album/${{pid}}/${{p.filename}}" onclick="window.open(this.src)">
        <div style="flex:1">
          <div style="font-weight:bold;font-size:13px">${{p.filename}}</div>
          <div class="meta">${{new Date(p.mtime*1000).toLocaleString('ja-JP')}} · ${{Math.round(p.size/1024)}}KB</div>
          <div class="meta">既読: ${{p.read_by.join(', ') || 'なし'}} · ${{p.locked ? '🔒' : ''}}</div>
          <div class="row" style="margin-top:6px;gap:4px">
            <button onclick="lockPhoto('${{pid}}','${{p.filename}}')">${{p.locked ? '解錠' : '施錠'}}</button>
            <button class="danger" onclick="deletePhoto('${{pid}}','${{p.filename}}')">削除</button>
          </div>
        </div>
      </div>
    </div>`).join('');
}}

async function lockPhoto(pid, fn) {{
  await fetch(`/api/album/${{pid}}/${{fn}}/lock`, {{method:'POST'}});
  loadAlbum();
}}
async function deletePhoto(pid, fn) {{
  if (!confirm('削除する？')) return;
  await fetch(`/api/album/${{pid}}/${{fn}}`, {{method:'DELETE'}});
  loadAlbum();
}}

// Voice memo
async function uploadVoice() {{
  const file = document.getElementById('vm-file').files[0];
  if (!file) return alert('ファイルを選んでください');
  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', document.getElementById('vm-title').value);
  const pid = document.getElementById('vm-from').value;
  const r = await fetch(`/api/voice_memo/${{pid}}/upload`, {{method:'POST', body:fd}});
  const j = await r.json();
  if (j.ok) {{ alert('送った！'); loadVoiceMemos(); }}
  else alert('エラー: ' + JSON.stringify(j));
}}

async function loadVoiceMemos() {{
  const pid = document.getElementById('vm-view-id').value;
  const memos = await fetch(`/api/voice_memo/${{pid}}`).then(r => r.json());
  const el = document.getElementById('voice-list');
  if (!memos.length) {{ el.innerHTML = '<p style="color:#999;padding:8px">ボイスメモなし</p>'; return; }}
  el.innerHTML = memos.map(m => `
    <div class="card">
      <div style="font-weight:bold;font-size:13px">${{m.filename}}</div>
      <div class="meta">${{new Date(m.mtime*1000).toLocaleString('ja-JP')}} · ${{Math.round(m.size/1024)}}KB</div>
      <div class="meta">聴いた: ${{m.listened_by.join(', ') || 'なし'}} · ${{m.locked ? '🔒' : ''}}</div>
      <audio controls src="/api/voice_memo/${{pid}}/${{m.filename}}"></audio>
      <div class="row" style="margin-top:6px;gap:4px">
        <button onclick="lockVoice('${{pid}}','${{m.filename}}')">${{m.locked ? '解錠' : '施錠'}}</button>
        <button class="danger" onclick="deleteVoice('${{pid}}','${{m.filename}}')">削除</button>
      </div>
    </div>`).join('');
}}

async function lockVoice(pid, fn) {{
  await fetch(`/api/voice_memo/${{pid}}/${{fn}}/lock`, {{method:'POST'}});
  loadVoiceMemos();
}}
async function deleteVoice(pid, fn) {{
  if (!confirm('削除する？')) return;
  await fetch(`/api/voice_memo/${{pid}}/${{fn}}`, {{method:'DELETE'}});
  loadVoiceMemos();
}}

// Notebook
async function addNote() {{
  const r = await fetch('/api/notebook', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      author: document.getElementById('nb-author').value,
      content: document.getElementById('nb-content').value,
    }})
  }});
  document.getElementById('nb-content').value = '';
  loadNotebook();
}}

async function loadNotebook() {{
  const entries = await fetch('/api/notebook').then(r => r.json());
  const el = document.getElementById('notebook-list');
  if (!entries.length) {{ el.innerHTML = '<p style="color:#999;padding:8px">まだ何も書いてない</p>'; return; }}
  el.innerHTML = entries.map(e => `
    <div class="card">
      <div class="row"><strong>${{e.author}}</strong><span class="meta" style="margin-left:8px">${{e.date}}</span></div>
      <div style="margin-top:6px;white-space:pre-wrap;font-size:13px">${{e.content}}</div>
    </div>`).join('');
}}

// Mail
async function sendMail() {{
  const r = await fetch('/api/mail/send', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      from_id: document.getElementById('ml-from').value,
      to_id: document.getElementById('ml-to').value,
      subject: document.getElementById('ml-subject').value,
      body: document.getElementById('ml-body').value,
    }})
  }});
  const j = await r.json();
  if (j.ok) {{ alert('送信した！'); document.getElementById('ml-body').value = ''; loadMail('inbox'); }}
  else alert('エラー: ' + JSON.stringify(j));
}}

async function loadMail(filter = 'inbox') {{
  const data = await fetch(`/api/mailbox?filter=${{filter}}`).then(r => r.json());
  const el = document.getElementById('mail-list');
  if (!data.items.length) {{ el.innerHTML = '<p style="color:#999;padding:8px">メールなし</p>'; return; }}
  el.innerHTML = data.items.map(m => `
    <div class="card">
      <div class="row">
        <strong>${{m.sender}} → ${{m.recipient}}</strong>
        <span class="meta" style="margin-left:8px">${{m.date}}</span>
        ${{m.starred ? '<span class="badge">⭐</span>' : ''}}
      </div>
      <div style="margin-top:6px;white-space:pre-wrap;font-size:13px">${{m.content}}</div>
      <div class="row" style="margin-top:6px;gap:4px">
        <button onclick="toggleStar('${{m.filename}}', ${{!m.starred}})">${{m.starred ? 'スター解除' : '⭐スター'}}</button>
        <button onclick="toggleArchive('${{m.filename}}', ${{!m.archived}})">${{m.archived ? '受信トレイへ' : 'アーカイブ'}}</button>
      </div>
    </div>`).join('');
}}

async function toggleStar(fn, starred) {{
  await fetch(`/api/mailbox/${{fn}}/meta`, {{
    method: 'PATCH',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{starred}})
  }});
  loadMail('inbox');
}}
async function toggleArchive(fn, archived) {{
  await fetch(`/api/mailbox/${{fn}}/meta`, {{
    method: 'PATCH',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{archived}})
  }});
  loadMail('inbox');
}}

// Chat
async function loadChat() {{
  const log = await fetch('/api/chat/history').then(r => r.json());
  const el = document.getElementById('chat-list');
  el.innerHTML = log.map(e => `
    <div class="card">
      <div class="row"><strong>${{e.role}}</strong><span class="meta" style="margin-left:8px">${{new Date(e.timestamp).toLocaleString('ja-JP')}}</span></div>
      <div style="margin-top:4px;white-space:pre-wrap;font-size:13px">${{e.text}}</div>
    </div>`).join('') || '<p style="color:#999;padding:8px">まだ会話なし</p>';
  el.scrollTop = el.scrollHeight;
}}

async function sendChat() {{
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';
  await fetch('/api/chat', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{message}})
  }});
  loadChat();
}}

// Records
async function loadRecordsList() {{
  const files = await fetch('/api/records').then(r => r.json());
  const el = document.getElementById('records-files');
  el.innerHTML = files.map(f => `<button onclick="loadRecord('${{f}}')" style="margin:2px 4px 2px 0;font-size:12px">${{f}}</button>`).join('') || '<p style="color:#999;padding:8px">記録なし</p>';
  document.getElementById('records-detail').innerHTML = '';
}}

async function loadRecord(fn) {{
  const events = await fetch(`/api/records/${{fn}}`).then(r => r.json());
  const el = document.getElementById('records-detail');
  el.innerHTML = events.map(e => {{
    if (e.kind === 'text') return `<div class="card">💬 ${{e.text}}</div>`;
    if (e.kind === 'thinking') return `<div class="card meta">🤔 ${{e.text}}</div>`;
    if (e.kind === 'tool_call') return `<div class="card meta">🔧 ${{e.name}}(${{JSON.stringify(e.input)}})</div>`;
    if (e.kind === 'result') return `<div class="card meta">✅ ${{e.turns}}ターン · $${{e.cost_usd}}</div>`;
    return '';
  }}).join('');
}}

// Diary
async function loadDiary() {{
  const date = document.getElementById('diary-date').value;
  const j = await fetch(`/api/diary/${{date}}`).then(r => r.json());
  document.getElementById('diary-content').textContent = j.summary || `(${{j.count}}件の会話。まだ日記なし)`;
}}

async function summarizeDiary() {{
  const date = document.getElementById('diary-date').value;
  const j = await fetch(`/api/diary/${{date}}/summarize`, {{method: 'POST'}}).then(r => r.json());
  document.getElementById('diary-content').textContent = j.summary || '(会話がありませんでした)';
}}

// Load album on start
loadAlbum();
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
