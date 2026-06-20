"""Apply Alembic migrations (uses DATABASE_URL from environment)."""
from __future__ import annotations

from backend.app.migrations import upgrade_database_head

if __name__ == "__main__":
    upgrade_database_head(reason="cli", allow_sqlite=True)
