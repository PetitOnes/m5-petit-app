# M5 Petit App

## [日本語ページ](./README.md)

A dashboard server for M5 Petit. Supports **N human users × N characters** (v0.3+): drop a new directory under `PETIT_DATA_DIR/characters/` and a character shows up (no restart needed — only the M5 device watcher is fixed at startup), and add an account to `users.json` and a family member gets their own login. A setup like Airi's household (2 humans × 3 characters) is the general shape this is built for. v0.4 adds per-character conversation locking and group chat.

Use the web UI in your browser to manage a photo album, voice memos, a notebook, a mailbox, chat, records, and a diary. When more than one character is configured, a character tab strip appears at the top of the page (hidden when there's only one character — and only characters the logged-in user is allowed to see show up at all).

**Auth**: on first run, a setup page lets you create the first user (admin-equivalent, gets every character), and every page after that requires logging in (cookie session). Add a second family member's account with `scripts/add_user.py` (see below).

## Features

- 📷 **Album** — upload/list photos, mark read, lock (auto-resize + JPEG compression)
- 🎙️ **Voice memo** — upload/list audio files, playback management
- 📔 **Notebook** — read and write text notes
- ✉️ **Mailbox** — send/receive messages, mark read
- 💬 **Chat** — talk to Claude (the character) directly from the web UI. Automatic responses triggered by the M5's mic/camera/sensors are logged here too. Conversations with the same character are serialized behind a per-character lock ("one character = one mind") — while it's talking to someone else you'll see "talking with <name>…" (see "Conversation locking" below)
- 👨‍👩‍👧‍👦 **Group chat** (v0.4+) — message every character you can see that has `in_group: true` (the default) at once. Replies stream back one at a time as each character finishes, and each character gets the previous one's reply as context. The log is per logged-in user, never shared across the family
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

On first run there's no `users.json` yet, so you're redirected to the setup page (`/setup`) automatically. Create the first account there (id / display name / password / color) — that user gets access to every character. Afterwards, log in at `/login`.

## Adding a user (second and beyond)

Additional family accounts are added with `scripts/add_user.py` (there's no add-user UI in the browser yet).

```bash
# a user with access to every character (prompts for the password)
python3 scripts/add_user.py bob "Bob"

# restricted to specific characters
python3 scripts/add_user.py bob "Bob" --characters petit_a,petit_b --color "#7da8f5"
```

When that user logs in, only the character tabs listed in their `characters` show up. Any character not in that list 404s from the API, same as a character that doesn't exist.

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
| `in_group` | Whether this character takes part in group chat (v0.4+) | `true` |

## Conversation locking (one character = one mind)

Every call to a character (1:1 chat, group chat, M5 button auto-responses, diary generation — all of them) is serialized behind a lock keyed on the character id, so one character never carries on two conversations at once. Requests to a *different* character run fully in parallel.

- If someone else's request comes in while a character is busy, it queues behind the lock. The web UI polls `GET /api/<character_id>/chat/status` and shows "talking with <name>…" (your own other requests don't count as "busy" for this purpose).
- If a lock isn't released within 120 seconds (`CHAR_LOCK_TIMEOUT`), it's force-opened so a waiter doesn't starve — the claude CLI call itself already times out at the same 120s, so this is mostly a second line of defense.

## How auth works

- Users live in `PETIT_DATA_DIR/users.json` (id / display name / color / password hash / which characters they can see). Passwords are never stored in plaintext — they're hashed with the standard library's `hashlib.scrypt`.
- Logging in sets a cookie (`petit_session`) containing the user id and an expiry, signed (not encrypted — there's nothing secret in the payload) with HMAC-SHA256. The signing key auto-generates on first use at `PETIT_DATA_DIR/.session_secret`, written `0600`.
- The cookie is `HttpOnly` + `SameSite=Lax`. This is a lightweight, home-LAN-first implementation; if you expose it beyond your LAN, put something like Tailscale in front of it (TLS termination and IP restriction are out of scope here).
- A user's `characters` field of `"all"` grants every character; a list restricts them to just those ids. A character outside that list 404s exactly like one that doesn't exist — so a user can't tell "wrong id" from "not yours" from the outside.

## Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `VOICE_API_HOST` | ASR server host (for mic transcription) | — |
| `PETIT_DATA_DIR` | Data directory | `~/petit_data` |
| `PROJECT_DIR` | Project root for the Claude CLI and scripts | this file's parent directory |
| `PORT` | Port to listen on | `8765` |
| `CLAUDE_CLI_PATH` | Path to the claude CLI executable (overridable for tests) | `claude` |

The `USER_ID` env var has been removed (not compatible with v0.2.x) — the human side is now `users.json` accounts. The `CHARACTER_ID` / `CHARACTER_NAME` / `M5_HOST` / `M5_HOSTS` env vars were removed earlier (not compatible with v0.1.x either). Move per-character settings into `config.json` as above. Use the migration script below to move data from an old layout.

Data is stored under `PETIT_DATA_DIR` as follows. `petit_data/` is shared with other M5 Petit tools (like the MCP server), so paths m5-petit-app doesn't actually read/write are noted as such.

```
petit_data/
├── users.json                            # ★ human accounts (id/name/color/password_hash/characters)
├── .session_secret                       # ★ cookie signing key (auto-generated, 0600)
├── resources/petit.png                   # character image, etc. (used by other tools, optional)
├── mailbox/                              # mailbox (from_X_to_Y naming, already N×N)
├── notebook/notebook.json                # notebook (shared across the family, not character-scoped)
├── users/<user_id>/group_chat.json       # ★ group chat log (v0.4+: per user, never shared across users)
└── characters/<character_id>/
    ├── SOUL.md                           # character personality definition (optional; falls back to default text)
    ├── album/<person_id>/                # album (v0.2+: moved under the character)
    ├── voice_memo/<person_id>/           # voice memos (v0.2+: moved under the character)
    ├── chat_histories/<user_id>.json     # chat log (v0.3+: split per character x user)
    ├── state/.session-id.<user_id>       # ★ Claude CLI --resume session id (per character x user; M5 button presses use `.session-id._m5`)
    ├── stream_logs/*.jsonl               # records (raw Claude CLI transcripts)
    ├── diary/YYYY-MM-DD.txt              # diary (cached per date; aggregates every user who talked to this character that day)
    ├── config/
    │   ├── config.json                   # ★ character config (id/name/color/m5_hosts/in_group) — read by m5-petit-app
    │   ├── autonomous-mcp.json           # MCP server config (optional; falls back to PROJECT_DIR/autonomous-mcp.json)
    │   ├── settings.json                 # (used by other tools, not read by m5-petit-app)
    │   └── voice_settings.json           # (used by other tools, not read by m5-petit-app)
    ├── notes/XXXXX.md                    # the character's own notes (used by other tools, not read by m5-petit-app)
    └── state/last_session.txt            # (used by other tools, not read by m5-petit-app)
```

## Migrating from an older layout

`scripts/migrate_v0_layout.py` runs two migration steps, together or independently. Both are idempotent — already-migrated data is skipped and reported, never overwritten.

```bash
# see what would happen, without moving anything
python3 scripts/migrate_v0_layout.py --character-id <old CHARACTER_ID value> --default-user-id <id of the first user you created> --data-dir <PETIT_DATA_DIR path> --dry-run

# actually migrate
python3 scripts/migrate_v0_layout.py --character-id <old CHARACTER_ID value> --default-user-id <id of the first user you created> --data-dir <PETIT_DATA_DIR path>
```

- **v0.1.x → v0.2.x (Phase A, `--character-id`)**: moves `photo_album/` → `characters/<id>/album/` and `voice_memo/` → `characters/<id>/voice_memo/`. Afterwards, create `characters/<id>/config/config.json` yourself (the script doesn't write it), e.g. `{"id": "<id>", "name": "..."}`. Omit `--character-id` to skip this step.
- **v0.2.x → v0.3.x (Phase B, `--default-user-id`)**: for every character, files its old single `chat_histories/chat_history.json` under `chat_histories/<default-user-id>.json` (that id becomes "who this character was talking to" going forward). Natural to run this with the id you just created on the setup page. Omit `--default-user-id` to skip this step.

## Scripts

- `scripts/write_mailbox.py` — a CLI tool for writing a message to the mailbox.

  ```bash
  python3 scripts/write_mailbox.py <from_id> <to_id> <content>
  python3 scripts/write_mailbox.py <from_id> <to_id> --file <path>
  ```

- `scripts/add_user.py` — adds a user to `users.json` (see "Adding a user" above).
- `scripts/migrate_v0_layout.py` — migrates an older data layout forward (see above).

## Tests

```bash
uv run ruff check .
uv run pytest -v
```
