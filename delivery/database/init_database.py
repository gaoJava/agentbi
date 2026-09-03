"""Initialize AgentBI storage from the production SQLAlchemy metadata.

Usage from the repository root:
    python delivery/database/init_database.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.environ.setdefault("AGENTBI_DATABASE_URL", "sqlite:///./data/agentbi.db")

from agentbi.database import IdentityRepository


def main() -> None:
    database = IdentityRepository(os.environ["AGENTBI_DATABASE_URL"])
    database.initialize(
        seed_demo_accounts=False,
        user_password="not-used",
        admin_password="not-used",
    )
    print(f"AgentBI database initialized: {os.environ['AGENTBI_DATABASE_URL']}")


if __name__ == "__main__":
    main()
