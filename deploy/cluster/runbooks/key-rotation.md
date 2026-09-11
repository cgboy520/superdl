# 平台主密钥轮换 SOP

`SUPERDL_CONFIG_ENCRYPTION_KEY` 保护:`platform_settings` secret 行、`admin_users.totp_secret`、`instances.jupyter_token` 与 `env_encrypted`;同一把钥匙派生 API Key / 节点令牌 / 短信验证码的 HMAC 摘要密钥。怀疑泄漏或按季度轮换时走本流程。

机制(apps/api/app/core/crypto.py):密文格式 `enc:v2:<kid>:<b64>`,kid = 主密钥指纹(SHA-256 前 12 hex);解密按 kid 在「当前 + PREVIOUS」钥匙串里选钥;摘要读路径 candidates 兼容旧钥匙世代,**摘除 PREVIOUS 即作废旧钥匙签发的 API Key**。

## 步骤

1. 生成新钥匙(urlsafe-base64 的 32 字节):
   `python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`
2. 更新 `superdl-crypto` Secret(deploy/app/secrets.example.yaml 的键面):
   - `SUPERDL_CONFIG_ENCRYPTION_KEY` = 新钥匙
   - `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` = **旧**钥匙(不可写反)
3. 滚动重启消费方:`kubectl -n superdl rollout restart deploy/superdl-api deploy/superdl-worker
   deploy/superdl-worker-tenant-mgr deploy/superdl-worker-node-mgr deploy/superdl-worker-prewarm`
   (disk-ops 与 10-migrate-job 不消费 crypto,无需处理)
4. 验证:启动日志无 `platform_setting_decrypt_failed`;管理端「平台配置」secret 项预览正常;用一把既有 API Key 调一次服务端点。
5. 重加密存量密文到新 kid(摘要不可重算,不在此列):

```bash
kubectl -n superdl exec deploy/superdl-api -- python - <<'EOF'
import asyncio
from sqlalchemy import select
from app.core import crypto
from app.core.db import get_sessionmaker
from app.core.platform_config import SETTING_SPECS, PlatformSetting
from app.modules.adminapi.models import AdminUser

async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        n = 0
        for row in (await s.execute(select(PlatformSetting))).scalars():
            spec = SETTING_SPECS.get(row.key)
            if spec and spec.kind == "secret" and crypto.is_encrypted(row.value):
                row.value = crypto.encrypt_str(crypto.decrypt_str(row.value, aad=row.key), aad=row.key)
                n += 1
        for admin in (await s.execute(select(AdminUser))).scalars():
            if admin.totp_secret and crypto.is_encrypted(admin.totp_secret):
                aad = f"totp:{admin.id}"
                admin.totp_secret = crypto.encrypt_str(crypto.decrypt_str(admin.totp_secret, aad=aad), aad=aad)
                n += 1
        await s.commit()
        print(f"re-encrypted {n} rows")

asyncio.run(main())
EOF
```

`env_encrypted` 随用户改 env 重写,短信验证码与节点注册令牌短 TTL 自然过期,均不需手工处理。

## 摘除 PREVIOUS(轮换收尾)

满足全部条件才可把 `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` 清空并再次滚动重启:

- 第 5 步已执行且重跑输出 `re-encrypted 0 rows`;
- 全部用户已重签 API Key(未重签的 Key 在摘除即刻 401;轮换公告必须带这一条);
- 存量密文 kid 均为当前钥匙指纹(打印 kid:`python3 -c "import hashlib,base64;
  print(hashlib.sha256(base64.urlsafe_b64decode('<新钥匙>')).hexdigest()[:12])"`)。
