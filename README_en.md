# M5 Petit App

## [日本語ページ](./README.md)

A dashboard server for M5 Petit. Supports **1 human user × N characters** (v0.2+): drop a new directory under `PETIT_DATA_DIR/characters/` and the character shows up in the web UI with no restart needed (only the M5 device watcher is wired up at process startup).
Multi-user support (separate logins per family member) isn't implemented yet — right now one human talks to any number of characters; per-user auth and per-user chat history separation are planned for a later phase.

Use the web UI in your browser to manage a photo album, voice memos, a notebook, a mailbox, chat, records, and a diary. When more than one character is configured, a character tab strip appears at the top of the page (hidden when there's only one character).

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

## Adding a character

Create `PETIT_DATA_DIR/characters/<character_id>/config/config.json` and the character shows up (no server restart needed — only the M5 device watcher is fixed at startup, so that part alone needs a restart). A directory with no `config.json` still works, falling back to the directory name as the ID.

```json
{
  "id": "petit_a",
  "name": "Petit A",
  "color": "#fff262",
  "m5_hosts": "192.168.1.50",
  "in_group": true
}
```

| Field | Description | Default |
| --- | --- | --- |
| `id` | Character ID (falls back to the directory name) | directory name |
| `name` | Display name (used in the tab strip and header) | same as `id` |
| `color` | Character tab color (CSS color) | `#4a7c59` |
| `m5_hosts` | M5 device hostname/IP (comma-separated string or array) | none |
| `in_group` | Reserved field (Phase C: group-chat participation flag) | `true` |

## Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `USER_ID` | Human user ID | `user` |
| `VOICE_API_HOST` | ASR server host (for mic transcription) | — |
| `PETIT_DATA_DIR` | Data directory | `~/petit_data` |
| `PROJECT_DIR` | Project root for the Claude CLI and scripts | this file's parent directory |
| `PORT` | Port to listen on | `8765` |

The `CHARACTER_ID` / `CHARACTER_NAME` / `M5_HOST` / `M5_HOSTS` env vars have been removed (not compatible with v0.1.x). Move per-character settings into `config.json` as above. Use the migration script below to move data from the old layout.

Data is stored under `PETIT_DATA_DIR` as follows. `petit_data/` is shared with other M5 Petit tools (like the MCP server), so paths m5-petit-app doesn't actually read/write are noted as such.

```
petit_data/
├── resources/petit.png                   # character image, etc. (used by other tools, optional)
├── mailbox/                              # mailbox (from_X_to_Y naming, already N×N)
├── notebook/<user_id>/notebook.json      # notebook (shared, not character-scoped)
└── characters/<character_id>/
    ├── SOUL.md                           # character personality definition (optional; falls back to default text)
    ├── album/<person_id>/                # album (v0.2+: moved under the character)
    ├── voice_memo/<person_id>/           # voice memos (v0.2+: moved under the character)
    ├── chat_histories/chat_history.json  # chat log
    ├── stream_logs/*.jsonl               # records (raw Claude CLI transcripts)
    ├── diary/YYYY-MM-DD.txt              # diary (cached per date)
    ├── config/
    │   ├── config.json                   # ★ character config (id/name/color/m5_hosts/in_group) — read by m5-petit-app
    │   ├── autonomous-mcp.json           # MCP server config (optional; falls back to PROJECT_DIR/autonomous-mcp.json)
    │   ├── settings.json                 # (used by other tools, not read by m5-petit-app)
    │   └── voice_settings.json           # (used by other tools, not read by m5-petit-app)
    ├── notes/XXXXX.md                    # the character's own notes (used by other tools, not read by m5-petit-app)
    └── state/last_session.txt            # for session resume (used by other tools, not read by m5-petit-app)
```

## Migrating from v0.1.x

To move from v0.1.x (fixed `CHARACTER_ID` env var, `photo_album/` / `voice_memo/` shared at the top level) to v0.2.x, use `scripts/migrate_v0_layout.py`:

```bash
# see what would happen, without moving anything
python3 scripts/migrate_v0_layout.py --character-id <old CHARACTER_ID value> --data-dir <PETIT_DATA_DIR path> --dry-run

# actually move the files
python3 scripts/migrate_v0_layout.py --character-id <old CHARACTER_ID value> --data-dir <PETIT_DATA_DIR path>
```

`--character-id` defaults to the `CHARACTER_ID` env var if omitted. It moves `photo_album/` → `characters/<id>/album/` and `voice_memo/` → `characters/<id>/voice_memo/`; afterwards, create `characters/<id>/config/config.json` yourself (the script doesn't write it), e.g. `{"id": "<id>", "name": "..."}`. `chat_histories/`, `stream_logs/`, and `diary/` were already character-scoped in v0.1.x, so nothing changes there.

## Scripts

- `scripts/write_mailbox.py` — a CLI tool for writing a message to the mailbox.

  ```bash
  python3 scripts/write_mailbox.py <from_id> <to_id> <content>
  python3 scripts/write_mailbox.py <from_id> <to_id> --file <path>
  ```

- `scripts/migrate_v0_layout.py` — migrates v0.1.x data layout to v0.2.x (see above).

## Tests

```bash
uv run ruff check .
uv run pytest -v
```
