# 账户

手机号+短信验证码注册/登录、JWT 会话、SSH 公钥管理、实名认证入口。

## 数据模型

- `users`:phone 唯一、password_hash(可空,主走验证码)、status(active/frozen)、low_balance_warn_hours、token_version、verification_status、实名/企业/发票字段(id_name/id_number/company_*/invoice_title,可空)
- `ssh_keys`:user_id、name、public_key、fingerprint(SHA256,唯一)
- `sms_codes`:phone、code_hash(带密钥摘要,非明文)、purpose(register/login)、expires_at、used_at、attempts
- `used_refresh_tokens`:jti(PK)、user_id、expires_at、used_at

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/auth/sms-code` | 匿名+限流 | `{phone, purpose}` → 204;mock 渠道固定码 123456 并落日志 |
| `POST /api/v1/auth/register` | 匿名 | `{phone, sms_code, password?}` → `{access_token, refresh_token, user}`;条款勾选前后端强校验 |
| `POST /api/v1/auth/login` | 匿名 | `{phone, sms_code \| password}`;冻结用户报 `USER_FROZEN` |
| `POST /api/v1/auth/password/reset` | 匿名 | 验证码重置密码 |
| `POST /api/v1/auth/refresh` | refresh token | 轮换发放新 token 对 |
| `GET /api/v1/me` | user | 用户资料 |
| `PATCH /api/v1/me/warn-threshold` | user | 余额预警阈值 |
| `POST /api/v1/me/real-name` | user | 三要素实名 provider seam(阿里云实人 `Mobile3MetaSimpleVerify`,BizCode 1 一致 / 2 不一致 / 3 无记录;dev 默认 mock) |
| `GET/POST/DELETE /api/v1/ssh-keys` | user | 公钥 CRUD |

## 规则与不变量

- 用户端与管理端 JWT audience 隔离(`user` / `admin`);user token 访问管理端 API 一律 403。
- refresh token 一次性消费:jti 落 `used_refresh_tokens`,重放视为泄露并撤销该用户全部在外 token。
- `users.token_version` 是撤销闸,递增即令已签发 token 全部失效;冻结用户同步递增。
- 验证码失败计次 `attempts` ≥5 即作废(持久化在 DB,不可只存进程内);同 phone 60s 内重复发码报 `SMS_TOO_FREQUENT`。
- 发码限流按 IP(20/h)+ 手机号(10/日),验证码登录/注册路径同样限流;计数落 PG,见 [security.md](./security.md)。
- 短信发送失败时必须作废已落库的验证码并返 502,不能留下可用码。
- 公钥须为 ssh-ed25519 / ssh-rsa / ecdsa-*;指纹重复报 `SSH_KEY_DUPLICATE`,非法公钥报 `SSH_KEY_INVALID`。
- 身份证号只存脱敏值;充值是否强制实名由 `real_name_required_for_recharge` 开关控制。
- 所有写操作过审计中间件(actor/ip/result)。
