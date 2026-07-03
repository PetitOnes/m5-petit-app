#!/usr/bin/env python3
"""Write a message to the mailbox.

Usage:
    python3 write_mailbox.py <from_id> <to_id> <content>
    python3 write_mailbox.py <from_id> <to_id> --file <path>
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.getenv("PETIT_DATA_DIR", str(Path.home() / "petit_data")))
MAILBOX_DIR = DATA_DIR / "mailbox"

VALID_ID = re.compile(r"^[a-zA-Z0-9_]+$")


def main() -> int:
    if len(sys.argv) < 4:
        print(f"Usage: {sys.argv[0]} <from_id> <to_id> <content>", file=sys.stderr)
        print(f"       {sys.argv[0]} <from_id> <to_id> --file <path>", file=sys.stderr)
        return 1

    from_id, to_id = sys.argv[1], sys.argv[2]

    if not VALID_ID.match(from_id) or not VALID_ID.match(to_id):
        print(f"Error: IDs must be alphanumeric+underscore: {from_id}, {to_id}", file=sys.stderr)
        return 1

    if sys.argv[3] == "--file" and len(sys.argv) >= 5:
        file_path = Path(sys.argv[4])
        if not file_path.exists():
            print(f"Error: file not found: {file_path}", file=sys.stderr)
            return 1
        content = file_path.read_text(encoding="utf-8")
    else:
        content = " ".join(sys.argv[3:])

    if not content.strip():
        print("Error: content is empty", file=sys.stderr)
        return 1

    MAILBOX_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y%m%d_%H%M")
    filename = f"from_{from_id}_to_{to_id}_{now}.md"
    filepath = MAILBOX_DIR / filename

    if filepath.exists():
        for i in range(2, 100):
            filepath = MAILBOX_DIR / f"from_{from_id}_to_{to_id}_{now}_{i}.md"
            if not filepath.exists():
                break

    filepath.write_text(content, encoding="utf-8")
    print(filepath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
