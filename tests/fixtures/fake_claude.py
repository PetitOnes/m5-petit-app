#!/usr/bin/env python3
"""A fake `claude` CLI for tests: mimics `claude --print --output-format
stream-json`'s shape closely enough for main.py's `_extract_reply_and_session`
to parse it, without shelling out to the real Claude CLI or costing an API
call.

Wired in by setting the `CLAUDE_CLI_PATH` environment variable to this
script's path (see main.py's `call_claude()`). Behavior is controlled by
environment variables so several tests can share one script:

  FAKE_CLAUDE_DELAY   seconds to sleep before replying (default 0.2) —
                       simulates a slow model call, long enough for tests to
                       observe overlap/non-overlap between concurrent calls.
  FAKE_CLAUDE_REPLY    the reply text to emit (default: "echo: <message>").
  FAKE_CLAUDE_LOG      if set, append a start/end record (JSON line) to this
                       file for each invocation, so a test can verify whether
                       two calls' [start, end) intervals overlapped in time.
"""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> None:
    message = sys.argv[-1] if len(sys.argv) > 1 else ""
    delay = float(os.environ.get("FAKE_CLAUDE_DELAY", "0.2"))
    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    reply = os.environ.get("FAKE_CLAUDE_REPLY") or f"echo: {message}"

    start = time.time()
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "start", "message": message, "t": start}) + "\n")

    time.sleep(delay)

    end = time.time()
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "end", "message": message, "t": end}) + "\n")

    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}}))
    print(json.dumps({"type": "result", "session_id": "fake-session"}))


if __name__ == "__main__":
    main()
