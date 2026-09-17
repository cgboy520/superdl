"""Create the admin account when the admin table is empty; existing admins are left alone.

The password comes from SUPERDL_SEED_ADMIN_PASSWORD; when unset one is generated and written to
SUPERDL_BOOTSTRAP_PASSWORD_FILE (default /tmp/bootstrap-admin-password), never printed.
Usage: cd apps/api && uv run python scripts/bootstrap_admin.py
"""

import asyncio
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import setup_logging
from app.modules.adminapi.auth_service import ensure_bootstrap_admin
from app.modules.adminapi.models import AdminUser


async def main() -> None:
    setup_logging()
    settings = get_settings()
    sm = get_sessionmaker()
    async with sm() as session:
        has_admin = (
            await session.execute(select(AdminUser.id).limit(1))
        ).scalar_one_or_none() is not None
        if has_admin:
            print(  # noqa: T201
                "bootstrap_admin: admin exists, nothing changed (bootstrap only acts on an empty"
                " admin_users)"
            )
            return
        password = os.environ.get("SUPERDL_SEED_ADMIN_PASSWORD")
        generated = password is None
        password = password or secrets.token_urlsafe(18)
        await ensure_bootstrap_admin(session, password)
        await session.commit()
        if generated:
            out_path = os.environ.get(
                "SUPERDL_BOOTSTRAP_PASSWORD_FILE", "/tmp/bootstrap-admin-password"
            )
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(out_path, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(password)
            print(  # noqa: T201
                f"bootstrap_admin done [{settings.environment}]: username admin;"
                f" random password in {out_path} (0600, delete after reading)."
                " The first login enforces TOTP enrolment; change the password right after"
            )
        else:
            print(  # noqa: T201
                f"bootstrap_admin done [{settings.environment}]: username admin"
                " (password from SUPERDL_SEED_ADMIN_PASSWORD, not written to disk)"
            )


if __name__ == "__main__":
    asyncio.run(main())
