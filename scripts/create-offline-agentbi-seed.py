"""Create a credential-free AgentBI SQLite seed for offline delivery."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    args.target.parent.mkdir(parents=True, exist_ok=True)
    if args.target.exists():
        args.target.unlink()

    source = sqlite3.connect(args.source)
    target = sqlite3.connect(args.target)
    try:
        source.backup(target)
        for table in (
            "auth_sessions",
            "audit_events",
            "conversation_turns",
            "conversation_states",
            "llm_provider_configs",
            "supersonic_llm_bindings",
        ):
            target.execute(f'DELETE FROM "{table}"')
        target.execute(
            "UPDATE users SET failed_login_count=0, locked_until=NULL, "
            "last_login_at=NULL"
        )
        target.commit()
        target.execute("VACUUM")
    finally:
        target.close()
        source.close()


if __name__ == "__main__":
    main()
