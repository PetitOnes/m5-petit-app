"""Per-character lock (one character = one mind) and call_claude()."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from . import config
from .characters import char_dir
from .records import _stream_log_dir

# ===================== Character lock (Phase C) =====================
# One asyncio.Lock per character id. Every call_claude() invocation for a
# given character — chat, group chat, M5 button reactions, diary generation —
# acquires this lock first, so the character never runs two conversations at
# once ("1 character = 1 mind"). Requests for *other* characters are
# completely unaffected (separate lock per id).
#
# A waiter gives up waiting after config.CHAR_LOCK_TIMEOUT seconds and force-opens a
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
        await asyncio.wait_for(lock.acquire(), timeout=config.CHAR_LOCK_TIMEOUT)
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
        mcp_config = config.PROJECT_DIR / "autonomous-mcp.json"

    soul_file = data_dir / "SOUL.md"
    soul = soul_file.read_text(encoding="utf-8") if soul_file.exists() else f"あなたは{char_name}です。"

    now_str = datetime.now(config.TZ).strftime("%Y-%m-%d %H:%M (JST)")
    partner_name = (user.get("name") or user.get("id")) if user else None
    system_prompt = (
        f"{soul}\n\n"
        f"現在の日時: {now_str}\n"
        f"データディレクトリ: {data_dir}/\n"
        f"メールボックス: {config.MAILBOX_DIR}/\n"
        f"メール送信: `python3 {config.SCRIPTS_DIR}/write_mailbox.py {char_id} <宛先ユーザーID> '<内容>'`\n"
        + (f"今話しかけているのは{partner_name}です。\n" if partner_name else "")
    )

    session_key = user["id"] if user else "_m5"
    sf = _session_file(char_id, session_key)

    def _build_cmd(resume: bool) -> list[str]:
        cmd = [config.CLAUDE_CLI_PATH, "--print", "--system-prompt", system_prompt, "--output-format", "stream-json", "--verbose"]
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
            stream_path = stream_dir / f"{datetime.now(config.TZ).strftime('%Y%m%d_%H%M%S')}_{source}.jsonl"
            stream_path.write_text(raw, encoding="utf-8")
            if new_sid:
                sf.parent.mkdir(parents=True, exist_ok=True)
                sf.write_text(new_sid, encoding="utf-8")
            return reply
        except Exception as e:
            print(f"[call_claude:{char_id}] error: {e}")
            return ""
