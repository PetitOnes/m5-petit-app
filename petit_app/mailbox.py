"""Mailbox helpers and API."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import config

# ===================== Mailbox helpers =====================
# Mailbox is already N×N by filename convention (from_<sender>_to_<recipient>_
# <timestamp>.md) — no change needed for Phase A.

def _load_mailbox_meta() -> dict:
    if config.MAILBOX_METADATA_FILE.exists():
        try:
            return json.loads(config.MAILBOX_METADATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": 1, "mails": {}}


def _save_mailbox_meta(data: dict):
    config.MAILBOX_METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.MAILBOX_METADATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


router = APIRouter()


# ===================== Mailbox API =====================
# Already N×N via the from_X_to_Y filename convention — no endpoint changes
# needed. The recipient picker in the UI is now populated from /api/characters.

@router.get("/api/mailbox")
def api_mailbox(filter: str = "inbox", offset: int = 0, limit: int = 100):
    """List mail. filter: inbox / archived / starred / all"""
    config.MAILBOX_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(config.MAILBOX_DIR.iterdir(), reverse=True):
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


@router.post("/api/mail/send")
async def api_mail_send(req: MailSendRequest):
    valid = re.compile(r"^[a-zA-Z0-9_]+$")
    if not valid.match(req.from_id) or not valid.match(req.to_id):
        return JSONResponse({"error": "invalid ID"}, status_code=400)
    content = f"**{req.subject}**\n\n{req.body}".strip() if req.subject else req.body
    proc = await asyncio.create_subprocess_exec(
        "python3", str(config.SCRIPTS_DIR / "write_mailbox.py"), req.from_id, req.to_id, content,
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


@router.patch("/api/mailbox/{filename}/meta")
def api_mailbox_update_meta(filename: str, body: MailMetaUpdate):
    safe = Path(filename).name
    if not (config.MAILBOX_DIR / safe).exists():
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
