"""生产环境首个管理员引导(任意环境可跑;不种 SKU/镜像,那是 seed_dev.py 的事)。

用法(需 PG 已迁移):cd apps/api && uv run python scripts/bootstrap_admin.py
- `admin_users` 为空时创建用户名 admin 的 admin 角色账号,之后再跑直接退出(引导只此一次);
- 口令取 SUPERDL_SEED_ADMIN_PASSWORD(须 ≥12 字符、UTF-8 ≤72 字节),未设则随机生成并只打印这一次;
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
        password = os.environ.get("SUPERDL_SEED_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
        await ensure_bootstrap_admin(session, password)
        await session.commit()
        print(  # noqa: T201
            f"bootstrap_admin done [{settings.environment}]: 用户名 admin,口令 {password}"
            "——口令仅本次显示,请立即保存并在首次登录后修改"
        )


if __name__ == "__main__":
    asyncio.run(main())
