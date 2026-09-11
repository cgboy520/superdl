"""首个管理员引导(任意环境;走完整 Settings 校验):`admin_users` 为空时建 admin,否则退出。
口令取 SUPERDL_SEED_ADMIN_PASSWORD(≥12 字符、UTF-8 ≤72 字节),未设则随机生成写 0600 文件
(SUPERDL_BOOTSTRAP_PASSWORD_FILE,默认 /tmp/bootstrap-admin-password),不进 stdout。
首次登录强制绑定 TOTP;登录后须改密并建第二个 admin。

用法:cd apps/api && uv run python scripts/bootstrap_admin.py
"""

import asyncio
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许 scripts/ 直跑

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import setup_logging
from app.modules.adminapi.models import AdminUser
from app.modules.adminapi.service import ensure_bootstrap_admin


async def main() -> None:
    setup_logging()
    settings = get_settings()  # prod 下 fail-fast 校验在此触发
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
            # 随机口令写 0600 文件,不进 stdout
            out_path = os.environ.get(
                "SUPERDL_BOOTSTRAP_PASSWORD_FILE", "/tmp/bootstrap-admin-password"
            )
            # 必须带 O_NOFOLLOW 拒绝符号链接(Windows 无此旗标)
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
