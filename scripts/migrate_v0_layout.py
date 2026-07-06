#!/usr/bin/env python3
"""Migrate a v0.x (single-character) data layout to the Phase A (N-character) layout.

The v0.x server pinned itself to one character via the `CHARACTER_ID` env var
and kept the album and voice memos in directories shared by everyone
(`photo_album/`, `voice_memo/`, at the top of `PETIT_DATA_DIR`). Phase A moves
those under that character's own directory, since the character list is now
discovered by scanning `characters/*/config/config.json` instead.

This script only moves `photo_album/` -> `characters/<id>/album/` and
`voice_memo/` -> `characters/<id>/voice_memo/`. `chat_histories/`,
`stream_logs/`, and `diary/` were already stored under `characters/<id>/` in
v0.x, so nothing to do there.

Usage:
    python3 scripts/migrate_v0_layout.py --character-id petit [--data-dir ~/petit_data] [--dry-run]

If --character-id is omitted, the CHARACTER_ID env var is used (matching how
the v0.x server picked its character).
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
        help="the single character this data directory used to be pinned to (default: $CHARACTER_ID)",
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


def main() -> int:
    args = parse_args()

    if not args.character_id:
        print("Error: --character-id is required (or set CHARACTER_ID env var)", file=sys.stderr)
        print("This is the character your v0.x server was pinned to.", file=sys.stderr)
        return 1

    data_dir = Path(args.data_dir).expanduser()
    if not data_dir.is_dir():
        print(f"Error: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    char_dir = data_dir / "characters" / args.character_id
    if args.dry_run:
        print(f"[dry-run] would migrate data under {data_dir} to character '{args.character_id}'")
    else:
        print(f"Migrating data under {data_dir} to character '{args.character_id}' ...")
    char_dir.mkdir(parents=True, exist_ok=True)

    moved_any = False
    for old_name in OLD_DIRS:
        old_dir = data_dir / old_name
        if not old_dir.is_dir():
            continue
        new_dir = char_dir / NEW_NAMES[old_name]
        print(f"\n{old_name}/ -> characters/{args.character_id}/{NEW_NAMES[old_name]}/")
        merge_move(old_dir, new_dir, args.dry_run)
        moved_any = True

    # chat_histories/, stream_logs/, diary/ were already under characters/<id>/
    # in v0.x — sanity-check they're there, but don't touch them.
    already_char_scoped = ["chat_histories", "stream_logs", "diary"]
    present = [name for name in already_char_scoped if (char_dir / name).exists()]
    if present:
        print(f"\n(already character-scoped, untouched: {', '.join(present)})")

    if not moved_any:
        print("\nNothing to migrate — no top-level photo_album/ or voice_memo/ found."
              " (Already migrated, or this was never a v0.x layout.)")
        return 0

    print("\nDone." if not args.dry_run else "\n[dry-run] no files were actually moved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
