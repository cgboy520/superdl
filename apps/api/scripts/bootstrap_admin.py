"""生产环境首个管理员引导(任意环境可跑;不种 SKU/镜像,那是 seed_dev.py 的事)。

用法(需 PG 已迁移):cd apps/api && uv run python scripts/bootstrap_admin.py
- `admin_users` 为空时创建用户名 admin 的 admin 角色账号,之后再跑直接退出(引导只此一次);
- 口令取 SUPERDL_SEED_ADMIN_PASSWORD(须 ≥12 字符、UTF-8 ≤72 字节);未设则随机生成,
  绝不打印到 stdout(日志采集=Loki 长期留存):写 0600 文件(路径
  SUPERDL_BOOTSTRAP_PASSWORD_FILE,默认 /tmp/bootstrap-admin-password),读取后即删;
- 管理端两步验证开启时(默认),首次登录会强制绑定 TOTP;登录后请立即改密并建出第二个 admin
  (调账双人复核需要两个人)。
与 seed_dev.py 的分工:seed_dev 只允许 dev/test(它绕过生产配置校验直灌开发种子),
本脚本走完整的 Settings 校验(prod 缺配即拒启),只做管理员引导。
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
            # 随机口令绝不进 stdout(日志采集=Loki 长期留存):写 0600 文件,读取后即删。
            # os.open 带 mode 原子落盘:write_text+chmod 分两步会留出 0644 窗口
            out_path = os.environ.get(
                "SUPERDL_BOOTSTRAP_PASSWORD_FILE", "/tmp/bootstrap-admin-password"
            )
            # O_NOFOLLOW:防 /tmp 下预置符号链接把口令写进别人的文件(Windows 无此旗标)
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
