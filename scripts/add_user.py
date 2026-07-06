#!/usr/bin/env python3
"""Add a user to users.json (for every account after the first — the first
one is created through the /setup page in the browser).

Standalone on purpose (no import of main.py, same style as write_mailbox.py)
so it stays usable even if the server dependencies aren't installed.

Usage:
    python3 scripts/add_user.py <id> <name> [--characters all|char1,char2] [--color '#7da8f5']
    (prompts for a password; use --password only for scripting/CI, it's
    visible in shell history and process listings)
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

DATA_DIR = Path(os.getenv("PETIT_DATA_DIR", str(Path.home() / "petit_data")))
USERS_FILE = DATA_DIR / "users.json"

VALID_ID = re.compile(r"^[a-zA-Z0-9_]+$")
DEFAULT_USER_COLOR = "#7da8f5"


def hash_password(password: str, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id", help="user id (alphanumeric/underscore)")
    parser.add_argument("name", help="display name")
    parser.add_argument("--characters", default="all",
                         help='"all" (default) or a comma-separated list of character ids')
    parser.add_argument("--color", default=DEFAULT_USER_COLOR, help="CSS color for this user's tabs/labels")
    parser.add_argument("--password", default=None,
                         help="password (prompted interactively if omitted — preferred)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing user with this id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not VALID_ID.match(args.id):
        print(f"Error: id must be alphanumeric/underscore: {args.id}", file=sys.stderr)
        return 1

    password = args.password
    if not password:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: passwords didn't match", file=sys.stderr)
            return 1
    if not password:
        print("Error: password is empty", file=sys.stderr)
        return 1

    characters: str | list[str]
    characters = "all" if args.characters.strip().lower() == "all" else [
        c.strip() for c in args.characters.split(",") if c.strip()
    ]

    doc: dict = {"users": []}
    if USERS_FILE.exists():
        try:
            doc = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Error: couldn't parse {USERS_FILE}: {e}", file=sys.stderr)
            return 1

    existing = next((u for u in doc.get("users", []) if u.get("id") == args.id), None)
    if existing and not args.force:
        print(f"Error: user already exists: {args.id} (use --force to overwrite)", file=sys.stderr)
        return 1

    user = {
        "id": args.id,
        "name": args.name,
        "color": args.color,
        "password_hash": hash_password(password),
        "characters": characters,
    }
    if existing:
        doc["users"] = [user if u.get("id") == args.id else u for u in doc["users"]]
    else:
        doc.setdefault("users", []).append(user)

    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        USERS_FILE.chmod(0o600)
    except OSError:
        pass

    print(f"{'Updated' if existing else 'Added'} user '{args.id}' in {USERS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
