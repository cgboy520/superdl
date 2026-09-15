# 平台主密钥轮换 SOP

`SUPERDL_CONFIG_ENCRYPTION_KEY` 保护:`platform_settings` secret 行(AAD = 配置键)、`admin_users.totp_secret`(AAD `totp:<admin.id>`)、`instances.jupyter_token`(AAD `jupyter-token:<instance.uuid>`)、`instances.env_encrypted`(AAD `instance-env:<instance.uuid>`);同一把钥匙派生 HMAC 摘要子密钥:`users.id_number_hmac`(实名证件号)、服务端点 API Key 摘要、节点注册令牌摘要、短信验证码摘要。怀疑泄漏或按季度轮换时走本流程。

机制(`apps/api/app/core/crypto.py`):密文格式 `enc:v2:<kid>:<b64>`,kid = 主密钥指纹(SHA-256 前 12 hex),`crypto.is_encrypted()` 判形态;解密按 kid 在「当前 + PREVIOUS」钥匙串里选钥,未知 kid 拒;摘要读路径 candidates 兼容两个世代(当前、PREVIOUS),写路径只写当前世代。

## 前置:先在 dev 库跑通

每次执行前在本机 dev 库把第 5 步脚本完整跑一遍(不是只在首次):

```bash
cd apps/api
docker compose -f ../../deploy/app/compose.yaml up -d
gen() { python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"; }
export SUPERDL_CONFIG_ENCRYPTION_KEY=$(gen)
uv run alembic upgrade head && uv run python scripts/seed_dev.py
# 造存量密文:起 API,用户端建一台实例(带 env),管理端绑一次 TOTP,平台配置填一项 secret;然后停 API
export SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS=$SUPERDL_CONFIG_ENCRYPTION_KEY SUPERDL_CONFIG_ENCRYPTION_KEY=$(gen)
uv run python - <<'EOF'
# 第 5 步脚本正文
EOF
uv run python - <<'EOF'
# 再跑一次,须输出 re-encrypted 0 rows, skipped 0
EOF
uv run uvicorn app.main:app   # 启动无 decrypt 失败日志;实例详情能取到 Jupyter 链接;管理端 TOTP 能登录
```

## 步骤

1. 生成新钥匙(urlsafe-base64 的 32 字节):
   `python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`
2. 更新 `superdl-crypto` Secret(`deploy/app/secrets.example.yaml` 的键面):
   - `SUPERDL_CONFIG_ENCRYPTION_KEY` = 新钥匙
   - `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` = **旧**钥匙(不可写反)
3. 滚动重启消费方:`kubectl -n superdl rollout restart deploy/superdl-api deploy/superdl-worker
   deploy/superdl-worker-tenant-mgr deploy/superdl-worker-node-mgr deploy/superdl-worker-prewarm`
   (disk-ops 与 10-migrate-job 不消费 crypto,无需处理)
4. 验证:启动日志无 `platform_setting_decrypt_failed`;管理端「平台配置」secret 项预览正常;用一把既有 API Key 调一次服务端点;打开一台运行中实例的 Jupyter 链接。
5. 重加密存量密文到新 kid(四张表;已是当前 kid 的行跳过;解不开的行计入 skipped 并打印,须为 0):

```bash
kubectl -n superdl exec deploy/superdl-api -- python - <<'EOF'
import asyncio
import base64
import hashlib
import os

from sqlalchemy import select

from app.core import crypto
from app.core.db import get_sessionmaker
from app.core.platform_config import SETTING_SPECS, PlatformSetting
from app.modules.adminapi.models import AdminUser
from app.modules.orchestrator.models import Instance

CUR_KID = hashlib.sha256(base64.urlsafe_b64decode(os.environ["SUPERDL_CONFIG_ENCRYPTION_KEY"])).hexdigest()[:12]
stats = {"re-encrypted": 0, "skipped": 0}


def rotate(value: str | None, aad: str) -> str | None:
    """返回新密文;非密文、已是当前 kid、解不开(未知 kid)时返回 None。"""
    if not value or not crypto.is_encrypted(value) or value.split(":", 3)[2] == CUR_KID:
        return None
    try:
        plain = crypto.decrypt_str(value, aad=aad)
    except ValueError as exc:
        stats["skipped"] += 1
        print(f"skip {aad}: {exc}")
        return None
    stats["re-encrypted"] += 1
    return crypto.encrypt_str(plain, aad=aad)


async def main() -> None:
    async with get_sessionmaker()() as s:
        for row in (await s.execute(select(PlatformSetting))).scalars():
            spec = SETTING_SPECS.get(row.key)
            if spec and spec.kind == "secret" and (v := rotate(row.value, row.key)):
                row.value = v
        for admin in (await s.execute(select(AdminUser))).scalars():
            if v := rotate(admin.totp_secret, f"totp:{admin.id}"):
                admin.totp_secret = v
        for inst in (await s.execute(select(Instance))).scalars():
            if v := rotate(inst.jupyter_token, f"jupyter-token:{inst.uuid}"):
                inst.jupyter_token = v
            if v := rotate(inst.env_encrypted, f"instance-env:{inst.uuid}"):
                inst.env_encrypted = v
        await s.commit()
    print(f"re-encrypted {stats['re-encrypted']} rows, skipped {stats['skipped']}")


asyncio.run(main())
EOF
```

短信验证码与节点注册令牌短 TTL 自然过期,不需手工处理。

## 摘要密钥(不可重算)

HMAC 摘要由主密钥派生,读路径只兼容「当前 + PREVIOUS」两个世代,原文不落库、无法按新钥匙重算:

| 摘要 | 写路径 | 摘除 PREVIOUS 后 |
|---|---|---|
| `users.id_number_hmac`(`hash_id_number`) | 实名通过时写入,库里只存打码证件号 | 旧世代用户的同证件账号上限(`real_name_max_accounts_per_identity`)对其失效,直到该用户重新实名 |
| 服务端点 API Key `key_hash`(`hash_api_key`) | 签发时 | 旧世代 Key 即刻 401,用户须重签 |
| 节点注册令牌(`hash_node_token`)、短信验证码(`hash_sms_code`) | 短 TTL | 自然过期,无需处理 |

**受支持的做法:轮换后 `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` 永久保留**(candidates 读路径同时查两代,保留无功能代价)。只在确认旧钥匙已泄露时才摘除,摘除即接受上表后果并公告 API Key 重签。钥匙串只有两代:第二次轮换会把 PREVIOUS 换成上一代,第一代钥匙签发的 `id_number_hmac` 与 API Key 摘要从此不再匹配;需要多代轮换而不丢实名去重,要先改代码(摘要密钥与加密主密钥分离),不在本 SOP 范围。

## 摘除 PREVIOUS(仅旧钥匙已泄露时)

满足全部条件才可把 `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` 清空并再次滚动重启:

- 第 5 步已执行且重跑输出 `re-encrypted 0 rows, skipped 0`;
- 全部用户已重签 API Key(未重签的 Key 在摘除即刻 401;轮换公告必须带这一条);
- 已接受旧世代 `users.id_number_hmac` 失效(上表);
- 存量密文 kid 均为当前钥匙指纹(打印 kid:`python3 -c "import hashlib,base64;
  print(hashlib.sha256(base64.urlsafe_b64decode('<新钥匙>')).hexdigest()[:12])"`)。
