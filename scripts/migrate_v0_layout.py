#!/usr/bin/env python3
"""Migrate old data layouts forward: v0.x (single-character) -> Phase A
(N-character) -> Phase B (N-user chat history split).

Phase A step (`--character-id`):
    The v0.x server pinned itself to one character via the `CHARACTER_ID` env
    var and kept the album and voice memos in directories shared by everyone
    (`photo_album/`, `voice_memo/`, at the top of `PETIT_DATA_DIR`). This
    moves those under that character's own directory, since the character
    list is now discovered by scanning `characters/*/config/config.json`
    instead. `chat_histories/`, `stream_logs/`, and `diary/` were already
    stored under `characters/<id>/` in v0.x, so nothing to do there.

Phase B step (`--default-user-id`):
    Phase A stored one chat log per character at
    `characters/<id>/chat_histories/chat_history.json`. Phase B splits chat
    history per (character, user): `characters/<id>/chat_histories/<user_id>.json`.
    This files that old single log under `--default-user-id` for every
    character that still has one — typically the id of the first user you
    create via the setup page. Runs across *all* characters (not just the one
    passed to `--character-id`, since Phase A already supports several).

Both steps are idempotent: already-migrated data (target already exists) is
left alone and reported, never overwritten.

Usage:
    # Phase A only (photo_album/voice_memo -> characters/<id>/...):
    python3 scripts/migrate_v0_layout.py --character-id petit [--data-dir ~/petit_data] [--dry-run]

    # Phase B only (chat_history.json -> chat_histories/<user_id>.json, all characters):
    python3 scripts/migrate_v0_layout.py --default-user-id arisan [--data-dir ~/petit_data] [--dry-run]

    # Both at once:
    python3 scripts/migrate_v0_layout.py --character-id petit --default-user-id arisan
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

OLD_DIRS = ["photo_album", "voice_memo"]
NEW_NAMES = {"photo_album": "album", "voice_memo": "voice_memo"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--character-id",
        default=os.environ.get("CHARACTER_ID"),
        help="Phase A step: the single character this data directory used to be pinned to "
             "(default: $CHARACTER_ID). Omit to skip the Phase A step.",
    )
    parser.add_argument(
        "--default-user-id",
        default=None,
        help="Phase B step: user id to file each character's old chat_history.json under "
             "(chat_histories/<user_id>.json). Omit to skip the Phase B step.",
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("PETIT_DATA_DIR", str(Path.home() / "petit_data")),
        help="PETIT_DATA_DIR to migrate (default: $PETIT_DATA_DIR or ~/petit_data)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing")
    return parser.parse_args()


def merge_move(src: Path, dst: Path, dry_run: bool) -> None:
    """Move src/* into dst/ (dst may already exist and contain files)."""
    dst.mkdir(parents=True, exist_ok=True)
    for child in sorted(src.iterdir()):
        target = dst / child.name
        if target.exists():
            print(f"  ! skip (already exists at destination): {child} -> {target}")
            continue
        print(f"  {child} -> {target}")
        if not dry_run:
            shutil.move(str(child), str(target))
    if not dry_run and not any(src.iterdir()):
        src.rmdir()


def migrate_phase_a(data_dir: Path, character_id: str, dry_run: bool) -> None:
    char_dir = data_dir / "characters" / character_id
    if dry_run:
        print(f"[dry-run] would migrate data under {data_dir} to character '{character_id}'")
    else:
        print(f"Migrating data under {data_dir} to character '{character_id}' ...")
    char_dir.mkdir(parents=True, exist_ok=True)

    moved_any = False
    for old_name in OLD_DIRS:
        old_dir = data_dir / old_name
        if not old_dir.is_dir():
            continue
        new_dir = char_dir / NEW_NAMES[old_name]
        print(f"\n{old_name}/ -> characters/{character_id}/{NEW_NAMES[old_name]}/")
        merge_move(old_dir, new_dir, dry_run)
        moved_any = True

    # chat_histories/, stream_logs/, diary/ were already under characters/<id>/
    # in v0.x — sanity-check they're there, but don't touch them.
    already_char_scoped = ["chat_histories", "stream_logs", "diary"]
    present = [name for name in already_char_scoped if (char_dir / name).exists()]
    if present:
        print(f"\n(already character-scoped, untouched: {', '.join(present)})")

    if not moved_any:
        print("\nNothing to migrate for Phase A — no top-level photo_album/ or voice_memo/ found."
              " (Already migrated, or this was never a v0.x layout.)")
    else:
        print("\nPhase A step done." if not dry_run else "\n[dry-run] Phase A: no files were actually moved.")


def migrate_phase_b(data_dir: Path, default_user_id: str, dry_run: bool) -> None:
    """Move characters/<id>/chat_histories/chat_history.json (Phase A's one
    log per character) to characters/<id>/chat_histories/<default_user_id>.json
    (Phase B's one log per character x user), for every character that has
    one. Idempotent: already-split characters (no chat_history.json left, or
    a same-named target already there) are skipped."""
    chars_dir = data_dir / "characters"
    if not chars_dir.is_dir():
        print(f"\nNothing to migrate for Phase B — no characters/ dir under {data_dir}.")
        return

    print(f"\nFiling old single-file chat histories under user '{default_user_id}' "
          f"(chat_history.json -> chat_histories/{default_user_id}.json) ...")
    found_any = False
    for char_d in sorted(chars_dir.iterdir()):
        if not char_d.is_dir():
            continue
        old = char_d / "chat_histories" / "chat_history.json"
        if not old.exists():
            continue
        found_any = True
        new = char_d / "chat_histories" / f"{default_user_id}.json"
        if new.exists():
            print(f"  ! skip (already exists at destination): {old} -> {new}")
            continue
        print(f"  {old} -> {new}")
        if not dry_run:
            shutil.move(str(old), str(new))

    if not found_any:
        print("\nNothing to migrate for Phase B — no characters/*/chat_histories/chat_history.json found."
              " (Already migrated, or every character is new since Phase B.)")
    else:
        print("\nPhase B step done." if not dry_run else "\n[dry-run] Phase B: no files were actually moved.")


def main() -> int:
    args = parse_args()

    if not args.character_id and not args.default_user_id:
        print("Error: nothing to do — pass --character-id (Phase A step), --default-user-id (Phase B step), "
              "or both.", file=sys.stderr)
        return 1

    data_dir = Path(args.data_dir).expanduser()
    if not data_dir.is_dir():
        print(f"Error: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    if args.character_id:
        migrate_phase_a(data_dir, args.character_id, args.dry_run)

    if args.default_user_id:
        migrate_phase_b(data_dir, args.default_user_id, args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
