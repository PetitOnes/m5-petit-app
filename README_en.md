# M5 Petit App

## [日本語ページ](./README.md)

A dashboard server for M5 Petit. Runs as a simple, always-on server for a single-user / single-M5 setup.

Use the web UI in your browser to manage a photo album, voice memos, a notebook, a mailbox, chat, records, and a diary.

## Features

- 📷 **Album** — upload/list photos, mark read, lock (auto-resize + JPEG compression)
- 🎙️ **Voice memo** — upload/list audio files, playback management
- 📔 **Notebook** — read and write text notes
- ✉️ **Mailbox** — send/receive messages, mark read
- 💬 **Chat** — talk to Claude (the character) directly from the web UI. Automatic responses triggered by the M5's mic/camera/sensors are logged here too
- 📜 **Records** — browse the raw transcript of each Claude CLI call (what it said, thought, and which tools it used)
- 📖 **Diary** — generate and view a diary written from the character's perspective, based on that day's chat log (with a manual "write" button)

## Setup

Install uv first if you don't already have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then install dependencies and run:

```bash
uv sync
uv run m5-petit-app
# or
uv run uvicorn main:app --port 8765
```

Runs on `http://localhost:8765` by default. The [Claude Code](https://docs.claude.com/claude-code) CLI (the `claude` command) is required for chat and diary generation.

## Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `CHARACTER_ID` | M5 character ID (alphanumeric, used in file paths etc.) | `petit` |
| `CHARACTER_NAME` | Display name (used in the header and default SOUL text; can be Japanese) | same as `CHARACTER_ID` |
| `USER_ID` | Human user ID | `user` |
| `M5_HOST` / `M5_HOSTS` | Hostname/IP of the M5 device (comma-separated for fallback) | — |
| `VOICE_API_HOST` | ASR server host (for mic transcription) | — |
| `PETIT_DATA_DIR` | Data directory | `~/petit_data` |
| `PROJECT_DIR` | Project root for the Claude CLI and scripts | this file's parent directory |
| `PORT` | Port to listen on | `8765` |

Data is stored under `PETIT_DATA_DIR` as follows. `petit_data/` is shared with other M5 Petit tools (like the MCP server), so paths m5-petit-app doesn't actually read/write are noted as such.

```
petit_data/
├── resources/petit.png                   # character image, etc. (used by other tools, optional)
├── mailbox/                              # mailbox
├── photo_album/<character_id|user_id>/   # album
├── voice_memo/<character_id|user_id>/    # voice memos
├── notebook/<user_id>/notebook.json      # notebook
└── characters/<character_id>/
    ├── SOUL.md                           # character personality definition (optional; falls back to default text)
    ├── chat_histories/chat_history.json  # chat log
    ├── stream_logs/*.jsonl               # records (raw Claude CLI transcripts)
    ├── diary/YYYY-MM-DD.txt              # diary (cached per date)
    ├── config/
    │   ├── autonomous-mcp.json           # MCP server config (optional; falls back to PROJECT_DIR/autonomous-mcp.json)
    │   ├── config.json                   # (used by other tools, not read by m5-petit-app)
    │   ├── settings.json                 # (used by other tools, not read by m5-petit-app)
    │   └── voice_settings.json           # (used by other tools, not read by m5-petit-app)
    ├── notes/XXXXX.md                    # the character's own notes (used by other tools, not read by m5-petit-app)
    └── state/last_session.txt            # for session resume (used by other tools, not read by m5-petit-app)
```

## Scripts

`scripts/write_mailbox.py` — a CLI tool for writing a message to the mailbox.

```bash
python3 scripts/write_mailbox.py <from_id> <to_id> <content>
python3 scripts/write_mailbox.py <from_id> <to_id> --file <path>
```
