"""Tests for Phase C: per-character conversation locking ("1 character = 1
mind"), the lock-timeout safety valve, and group chat (sequential streaming,
per-user log isolation, in_group filtering).

These exercise call_claude() against the fake `claude` CLI wired in by
conftest.py's `app_module` fixture (tests/fixtures/fake_claude.py) rather
than shelling out to the real Claude CLI. Concurrency assertions use
httpx.AsyncClient + asyncio.gather against the FastAPI app directly (not the
synchronous TestClient), since we need two requests genuinely in flight at
once in the same event loop.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx

from conftest import make_character as _make_character


def _client(app_module) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app_module.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _setup_and_login(app_module, client: httpx.AsyncClient, user_id: str, name: str, password: str = "pw") -> None:
    """First user in a fresh app_module — uses /api/setup, gets `characters: all`."""
    r = await client.post("/api/setup", json={"id": user_id, "name": name, "password": password, "color": "#7da8f5"})
    assert r.status_code == 200, r.text
    r = await client.post("/api/auth/login", json={"id": user_id, "password": password})
    assert r.status_code == 200, r.text


async def _add_user_and_login(app_module, client: httpx.AsyncClient, user_id: str, name: str,
                               password: str = "pw", characters="all") -> None:
    app_module.create_user(user_id, name, password, characters=characters)
    r = await client.post("/api/auth/login", json={"id": user_id, "password": password})
    assert r.status_code == 200, r.text


def _read_log(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _interval_for(entries: list[dict], message: str) -> tuple[float, float]:
    starts = [e["t"] for e in entries if e["event"] == "start" and e["message"] == message]
    ends = [e["t"] for e in entries if e["event"] == "end" and e["message"] == message]
    assert starts and ends, f"no log entries for message={message!r}: {entries}"
    return starts[0], ends[0]


# ===================== Character lock: serialization =====================

async def test_same_character_concurrent_requests_serialize(app_module, tmp_path, monkeypatch):
    _make_character(tmp_path, "alpha")
    log_file = tmp_path / "fake_claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log_file))
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "0.3")

    async with _client(app_module) as alice, _client(app_module) as bob:
        await _setup_and_login(app_module, alice, "alice", "Alice")
        await _add_user_and_login(app_module, bob, "bob", "Bob")

        r_alice, r_bob = await asyncio.gather(
            alice.post("/api/alpha/chat", json={"message": "hello-alice"}),
            bob.post("/api/alpha/chat", json={"message": "hello-bob"}),
        )
    assert r_alice.status_code == 200, r_alice.text
    assert r_bob.status_code == 200, r_bob.text

    entries = _read_log(log_file)
    # main.py appends the (possibly context-wrapped) message as the fake
    # CLI's last positional arg, so the raw text we sent shows up verbatim.
    start_a, end_a = _interval_for(entries, "hello-alice")
    start_b, end_b = _interval_for(entries, "hello-bob")
    # Serialized: whichever ran second must not have started before the
    # first one finished (small epsilon for clock resolution).
    assert (end_a <= start_b + 0.01) or (end_b <= start_a + 0.01), (
        f"expected non-overlapping intervals, got a=({start_a},{end_a}) b=({start_b},{end_b})"
    )


async def test_different_characters_run_concurrently(app_module, tmp_path, monkeypatch):
    _make_character(tmp_path, "alpha")
    _make_character(tmp_path, "beta")
    log_file = tmp_path / "fake_claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log_file))
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "0.3")

    async with _client(app_module) as alice:
        await _setup_and_login(app_module, alice, "alice", "Alice")
        t0 = time.monotonic()
        r_alpha, r_beta = await asyncio.gather(
            alice.post("/api/alpha/chat", json={"message": "hi-alpha"}),
            alice.post("/api/beta/chat", json={"message": "hi-beta"}),
        )
        elapsed = time.monotonic() - t0

    assert r_alpha.status_code == 200
    assert r_beta.status_code == 200
    # Two different characters' locks are independent, so both 0.3s calls
    # should run in parallel — comfortably under 2x the delay.
    assert elapsed < 0.5, f"expected concurrent execution, took {elapsed:.2f}s"

    entries = _read_log(log_file)
    start_a, end_a = _interval_for(entries, "hi-alpha")
    start_b, end_b = _interval_for(entries, "hi-beta")
    overlap = not (end_a <= start_b or end_b <= start_a)
    assert overlap, f"expected overlapping intervals, got a=({start_a},{end_a}) b=({start_b},{end_b})"


async def test_chat_status_reports_busy_partner_while_locked(app_module, tmp_path, monkeypatch):
    _make_character(tmp_path, "alpha")
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "0.3")

    async with _client(app_module) as alice, _client(app_module) as bob:
        await _setup_and_login(app_module, alice, "alice", "Alice")
        await _add_user_and_login(app_module, bob, "bob", "Bob")

        task = asyncio.create_task(alice.post("/api/alpha/chat", json={"message": "hi"}))
        await asyncio.sleep(0.1)  # let alice's request acquire the lock

        status = await bob.get("/api/alpha/chat/status")
        assert status.status_code == 200
        body = status.json()
        assert body["busy"] is True
        assert body["partner_name"] == "Alice"

        await task

        status_after = await bob.get("/api/alpha/chat/status")
        assert status_after.json() == {"busy": False, "partner_name": None}


async def test_lock_force_released_after_timeout(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "CHAR_LOCK_TIMEOUT", 0.05)
    stuck_lock = app_module._get_char_lock("alpha")
    await stuck_lock.acquire()  # simulate a wedged holder that never releases

    t0 = time.monotonic()
    async with app_module._character_lock("alpha", "bob", "Bob"):
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5  # forced open well before a real 120s wait
        assert app_module.character_lock_status("alpha")["name"] == "Bob"

    assert app_module.character_lock_status("alpha") is None


# ===================== Group chat =====================

async def test_group_chat_stream_ndjson_one_at_a_time_in_order(app_module, tmp_path, monkeypatch):
    _make_character(tmp_path, "alpha", name="Alpha")
    _make_character(tmp_path, "beta", name="Beta")
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "0.05")

    async with _client(app_module) as alice:
        await _setup_and_login(app_module, alice, "alice", "Alice")
        async with alice.stream("POST", "/api/group/chat/stream", json={"message": "yo everyone"}) as resp:
            assert resp.status_code == 200
            lines = [json.loads(line) async for line in resp.aiter_lines() if line.strip()]

    assert [r["character_id"] for r in lines] == ["alpha", "beta"]
    for r in lines:
        assert r["reply"]  # fake CLI always returns non-empty text
        assert "name" in r and "color" in r


async def test_group_chat_log_persisted_and_isolated_per_user(app_module, tmp_path, monkeypatch):
    _make_character(tmp_path, "alpha")
    _make_character(tmp_path, "beta")
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "0.02")

    async with _client(app_module) as alice, _client(app_module) as bob:
        await _setup_and_login(app_module, alice, "alice", "Alice")
        await _add_user_and_login(app_module, bob, "bob", "Bob")

        r = await alice.post("/api/group/chat/stream", json={"message": "alice's group message"})
        assert r.status_code == 200
        r = await bob.post("/api/group/chat/stream", json={"message": "bob's group message"})
        assert r.status_code == 200

        alice_log = await alice.get("/api/group/history")
        bob_log = await bob.get("/api/group/history")

    alice_texts = [e["text"] for e in alice_log.json()]
    bob_texts = [e["text"] for e in bob_log.json()]
    assert "alice's group message" in alice_texts
    assert "alice's group message" not in bob_texts
    assert "bob's group message" in bob_texts
    assert "bob's group message" not in alice_texts

    # Design decision (5): stored under users/<user_id>/, not shared.
    assert (tmp_path / "users" / "alice" / "group_chat.json").exists()
    assert (tmp_path / "users" / "bob" / "group_chat.json").exists()


async def test_group_chat_excludes_characters_with_in_group_false(app_module, tmp_path, monkeypatch):
    _make_character(tmp_path, "alpha", in_group=True)
    _make_character(tmp_path, "beta", in_group=False)
    _make_character(tmp_path, "gamma")  # default True
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "0.02")

    async with _client(app_module) as alice:
        await _setup_and_login(app_module, alice, "alice", "Alice")
        async with alice.stream("POST", "/api/group/chat/stream", json={"message": "hi"}) as resp:
            lines = [json.loads(line) async for line in resp.aiter_lines() if line.strip()]

    ids = {r["character_id"] for r in lines}
    assert ids == {"alpha", "gamma"}


async def test_group_chat_only_includes_characters_visible_to_user(app_module, tmp_path, monkeypatch):
    _make_character(tmp_path, "alpha")
    _make_character(tmp_path, "beta")
    monkeypatch.setenv("FAKE_CLAUDE_DELAY", "0.02")

    async with _client(app_module) as bob:
        await _setup_and_login(app_module, bob, "admin", "Admin")
        app_module.create_user("bob", "Bob", "pw", characters=["alpha"])
        r = await bob.post("/api/auth/logout")
        assert r.status_code == 200
        r = await bob.post("/api/auth/login", json={"id": "bob", "password": "pw"})
        assert r.status_code == 200

        async with bob.stream("POST", "/api/group/chat/stream", json={"message": "hi"}) as resp:
            lines = [json.loads(line) async for line in resp.aiter_lines() if line.strip()]

    assert {r["character_id"] for r in lines} == {"alpha"}
