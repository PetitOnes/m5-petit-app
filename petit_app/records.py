"""Records API: raw claude stream-json logs per character."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .auth import get_current_user
from .characters import char_dir, require_character

router = APIRouter()


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


@router.get("/api/{character_id}/records")
async def api_records_list(character_id: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    return _list_stream_logs(character_id)


@router.get("/api/{character_id}/records/{filename}")
async def api_records_get(character_id: str, filename: str, user: dict = Depends(get_current_user)):
    require_character(character_id, user)
    if not filename.endswith(".jsonl") or "/" in filename or ".." in filename:
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    path = _stream_log_dir(character_id) / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return _parse_stream_log(path)
