"""管理员表为空时创建 admin;已有管理员时不作修改。

口令取 SUPERDL_SEED_ADMIN_PASSWORD;未设置时随机生成并写入
SUPERDL_BOOTSTRAP_PASSWORD_FILE(默认 /tmp/bootstrap-admin-password),不打印口令。
用法:cd apps/api && uv run python scripts/bootstrap_admin.py
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
            print("bootstrap_admin: admin 已存在,未改动(引导只在 admin_users 为空时生效)")  # noqa: T201
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
                f"bootstrap_admin done [{settings.environment}]: 用户名 admin;"
                f"随机口令在 {out_path}(0600,读取后即删)。首次登录强制绑定 TOTP,登录后请立即改密"
            )
        else:
            print(  # noqa: T201
                f"bootstrap_admin done [{settings.environment}]: 用户名 admin"
                "(口令来自 SUPERDL_SEED_ADMIN_PASSWORD,未落盘)"
            )


if __name__ == "__main__":
    asyncio.run(main())
