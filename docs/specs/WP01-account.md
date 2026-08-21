# WP1 · 账户

## 目标
手机号+短信验证码注册/登录、JWT 会话、SSH 公钥管理、审计中间件生效。

## 数据
- `users`:phone 唯一、password_hash(可空,MVP 主走验证码)、status(active/frozen)、实名/企业字段预留(id_name/id_number/company_*,全部可空)、created_at
- `ssh_keys`:user_id、name、public_key、fingerprint(SHA256,唯一)、created_at
- `sms_codes`:phone、code、purpose(register/login)、expires_at、used_at、created_at;同 phone 60s 限频

## API(/api/v1)
- `POST /auth/sms-code` {phone, purpose} → 204;mock 渠道固定码 123456 并落日志
- `POST /auth/register` {phone, sms_code, password?} → {access_token, refresh_token, user}
- `POST /auth/login` {phone, sms_code | password} → 同上;冻结用户报 USER_FROZEN
- `POST /auth/refresh` {refresh_token} → 新 token 对
- `GET /me` → 用户资料
- `GET/POST/DELETE /ssh-keys`;公钥格式校验(ssh-ed25519 / ssh-rsa / ecdsa-*),指纹去重

## 验收
- 验证码错误/过期/复用均拒;60s 内重复发送报 SMS_TOO_FREQUENT
- user token 访问 admin API 403(audience 隔离);过期 token 401
- 所有写操作在 audit_log 可见(actor/ip/result)
- 公钥指纹重复报 SSH_KEY_DUPLICATE;非法公钥报 SSH_KEY_INVALID
