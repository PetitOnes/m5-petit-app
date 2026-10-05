"""M5 WebSocket watcher: camera / sensor / mic button reactions."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import websockets

from . import config
from .locks import call_claude

# ===================== M5 watcher =====================

# Physical M5 button presses (camera/sensor/mic) aren't tied to any logged-in
# web session, so there's no specific user to name in those prompts (unlike
# chat, which always has one — see call_claude()'s `user` argument).
_M5_EVENT_ACTOR = "そばにいる人"


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
            f"誰かに伝えたければ `python3 {config.SCRIPTS_DIR}/write_mailbox.py {char_id} <宛先ユーザーID> '<感想>'` でメールを送って。"
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
    if len(pcm_bytes) < 512 or not config._ASR_URL:
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
                f"{config._ASR_URL}/transcribe",
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
