"""把库里残留的 `enc:v1:` 密文就地重写成 `enc:v2:`(一次性数据修复,幂等)。

v1 用主密钥直接做 AES-GCM 用钥,v2 改为经 HKDF 派生子密钥(core/crypto 的域分离),
两者前缀不同、用钥不同。`decrypt_str` 曾同时读 v1/v2,后来只留 v2 —— 读路径删掉时
存量 v1 密文没有回填,于是这些行一律解不开:管理员 MFA 登录 500(密文缺少 enc: 版本前缀)、
实例 Jupyter 令牌同理。本脚本补上那次遗漏的回填。

覆盖两处落库密文(其余 `encrypt_str` 落点若也残留 v1,加进 _TARGETS 即可):
- admin_users.totp_secret     AAD `totp:<id>`
- instances.jupyter_token     AAD `jupyter-token:<uuid>`
platform_settings.value 与 instances.env_encrypted 走同一套 crypto,已在 _TARGETS 内按需扩展。

用法(需 SUPERDL_CONFIG_ENCRYPTION_KEY 与写库时同一把;轮换过则另挂 _PREVIOUS):
    cd apps/api && uv run python scripts/reencrypt_v1_to_v2.py          # 演练,只报告
    cd apps/api && uv run python scripts/reencrypt_v1_to_v2.py --apply  # 落库
演练会把每一行都真解一次,全部可解才允许 --apply;任一行解不开即退出非零,不写半套。
"""

import asyncio
import base64
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许 scripts/ 直跑

from sqlalchemy import text

from app.core.crypto import _active_key, _previous_key, encrypt_str
from app.core.db import get_sessionmaker
from app.core.logging import setup_logging

_PREFIX_V1 = "enc:v1:"


@dataclass(frozen=True)
class Target:
    """一处密文落点:表、密文列、主键列,以及主键 → AAD 的构造方式。"""

    table: str
    column: str
    key_column: str
    aad_template: str  # 形如 "totp:{key}",{key} 由 key_column 的值填入

    def aad_for(self, key_value: object) -> str:
        return self.aad_template.format(key=key_value)


_TARGETS = (
    Target("admin_users", "totp_secret", "id", "totp:{key}"),
    Target("instances", "jupyter_token", "uuid", "jupyter-token:{key}"),
)


def _decrypt_v1(token: str, *, aad: str) -> str:
    """v1 解密:主密钥直接当 AES-GCM 用钥(无 HKDF),依次试当前钥与轮换旧钥。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    blob = base64.b64decode(token[len(_PREFIX_V1) :])
    last: Exception | None = None
    for key in (k for k in (_active_key(), _previous_key()) if k is not None):
        try:
            return AESGCM(key).decrypt(blob[:12], blob[12:], aad.encode()).decode()
        except Exception as exc:
            last = exc
    raise ValueError(f"v1 密文解密失败(主密钥不匹配?):{last}")


async def main() -> int:
    setup_logging()
    apply = "--apply" in sys.argv[1:]
    sessionmaker = get_sessionmaker()
    total = 0
    failures: list[str] = []

    async with sessionmaker() as session:
        for tgt in _TARGETS:
            rows = (
                await session.execute(
                    text(
                        f"SELECT {tgt.key_column} AS k, {tgt.column} AS c FROM {tgt.table} "
                        f"WHERE {tgt.column} LIKE :pat ORDER BY {tgt.key_column}"
                    ),
                    {"pat": f"{_PREFIX_V1}%"},
                )
            ).all()
            if not rows:
                print(f"  {tgt.table}.{tgt.column}: 无 v1 残留")  # noqa: T201
                continue
            print(f"  {tgt.table}.{tgt.column}: {len(rows)} 行待重写")  # noqa: T201
            for key_value, token in rows:
                aad = tgt.aad_for(key_value)
                try:
                    plaintext = _decrypt_v1(token, aad=aad)
                except Exception as exc:
                    failures.append(f"{tgt.table}.{tgt.column}[{key_value}]: {exc}")
                    continue
                total += 1
                if not apply:
                    continue
                await session.execute(
                    text(f"UPDATE {tgt.table} SET {tgt.column} = :v WHERE {tgt.key_column} = :k"),
                    {"v": encrypt_str(plaintext, aad=aad), "k": key_value},
                )

        if failures:
            print("\n解密失败,未写入任何行:")  # noqa: T201
            for f in failures:
                print(f"  - {f}")  # noqa: T201
            await session.rollback()
            return 1
        if apply:
            await session.commit()
            print(f"\n已重写 {total} 行为 enc:v2。")  # noqa: T201
        else:
            print(f"\n演练通过:{total} 行均可解密。加 --apply 落库。")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
