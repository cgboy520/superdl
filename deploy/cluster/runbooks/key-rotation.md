# Platform master key rotation SOP

`SUPERDL_CONFIG_ENCRYPTION_KEY` protects: `platform_settings` secret rows (AAD = the setting key), `admin_users.totp_secret` (AAD `totp:<admin.id>`), `instances.jupyter_token` (AAD `jupyter-token:<instance.uuid>`), `instances.env_encrypted` (AAD `instance-env:<instance.uuid>`); the same key derives the HMAC digest subkey for `users.kyc_identity_hmac` (KYC identity number), service endpoint API key digests, node enrollment token digests and verification code digests. Follow this procedure on suspected leak or for the quarterly rotation.

Mechanism (`apps/api/app/core/crypto.py`): ciphertext format `enc:v2:<kid>:<b64>`, kid = master key fingerprint (first 12 hex of SHA-256), `crypto.is_encrypted()` recognises the shape; decryption picks the key by kid from the "current + PREVIOUS" keyring and rejects unknown kids; digest read paths accept candidates of both generations (current, PREVIOUS), write paths write the current generation only.

## Prerequisite: run it on the dev database first

Before every execution run the step 5 script end to end against the local dev database (not only the first time):

```bash
cd apps/api
docker compose -f ../../deploy/app/compose.yaml up -d
gen() { python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"; }
export SUPERDL_CONFIG_ENCRYPTION_KEY=$(gen)
uv run alembic upgrade head && uv run python scripts/seed_dev.py
# produce existing ciphertexts: start the API, create one instance (with env) in the user console, enrol TOTP once in the admin console, fill one secret in platform configuration; then stop the API
export SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS=$SUPERDL_CONFIG_ENCRYPTION_KEY SUPERDL_CONFIG_ENCRYPTION_KEY=$(gen)
uv run python - <<'EOF'
# body of the step 5 script
EOF
uv run python - <<'EOF'
# run again, must print re-encrypted 0 rows, skipped 0
EOF
uv run uvicorn app.main:app   # no decrypt failure in the startup log; the instance detail returns the Jupyter link; admin TOTP login works
```

## Steps

1. Generate the new key (32 urlsafe-base64 bytes):
   `python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`
2. Update the `superdl-crypto` Secret (keys as in `deploy/app/secrets.example.yaml`):
   - `SUPERDL_CONFIG_ENCRYPTION_KEY` = the new key
   - `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` = the **old** key (do not swap them)
3. Rolling restart of the consumers: `kubectl -n superdl rollout restart deploy/superdl-api deploy/superdl-worker
   deploy/superdl-worker-tenant-mgr deploy/superdl-worker-node-mgr deploy/superdl-worker-prewarm`
   (disk-ops and 10-migrate-job do not consume crypto, nothing to do)
4. Verify: no `platform_setting_decrypt_failed` in the startup log; secret previews under admin "Platform configuration" render; call one service endpoint with an existing API key; open the Jupyter link of a running instance.
5. Re-encrypt existing ciphertexts to the new kid (four tables; rows already on the current kid are skipped; undecryptable rows count as skipped and are printed, must be 0):

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
    """Return the new ciphertext; None for non-ciphertext, current kid, or undecryptable (unknown kid)."""
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

Verification codes and node enrollment tokens have short TTLs and expire on their own; no manual work.

## Digest keys (cannot be recomputed)

The HMAC digests derive from the master key; read paths accept only the "current + PREVIOUS" generations, the plaintext is never stored, so digests cannot be recomputed under a new key:

| Digest | Write path | After dropping PREVIOUS |
|---|---|---|
| `users.kyc_identity_hmac` (`hash_kyc_identity`) | Written when KYC passes; the database stores only the masked identity number | The per-identity account cap (`real_name_max_accounts_per_identity`) stops applying to old-generation users until they pass KYC again |
| Service endpoint API key `key_hash` (`hash_api_key`) | At issue time | Old-generation keys answer 401 at once, users must issue new keys |
| Node enrollment tokens (`hash_node_token`), verification codes (`hash_verification_code`) | Short TTL | Expire on their own, nothing to do |

**The supported practice: keep `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` permanently after a rotation** (the candidate read path checks both generations at no functional cost). Drop it only once the old key is confirmed leaked, accepting the consequences above and announcing the API key re-issue. The keyring has two generations only: a second rotation replaces PREVIOUS with the previous generation, and the `kyc_identity_hmac` and API key digests issued under the first key stop matching from then on; multi-generation rotation without losing KYC deduplication needs a code change first (separating the digest key from the encryption master key), outside this SOP.

## Dropping PREVIOUS (only when the old key has leaked)

Clear `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` and roll the restart again only when every condition holds:

- step 5 has run and a re-run prints `re-encrypted 0 rows, skipped 0`;
- every user has re-issued their API keys (keys not re-issued answer 401 the moment PREVIOUS is dropped; the rotation announcement must say so);
- the loss of old-generation `users.kyc_identity_hmac` is accepted (table above);
- every existing ciphertext kid is the current key's fingerprint (print the kid: `python3 -c "import hashlib,base64;
  print(hashlib.sha256(base64.urlsafe_b64decode('<new key>')).hexdigest()[:12])"`).
