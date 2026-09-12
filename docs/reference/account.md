# 账户

手机号+短信验证码注册/登录、JWT 会话、SSH 公钥管理、实名认证入口。

## 数据模型

- `users`:phone 唯一、password_hash(可空)、status(active/frozen/deleted)、low_balance_warn_hours、token_version、verification_status、实名字段(id_name/id_number 脱敏串/id_number_hmac 带密钥摘要,可空)
- `ssh_keys`:user_id、name、public_key、fingerprint(SHA256,唯一)
- `sms_codes`:phone、code_hash(带密钥摘要)、purpose(register/login/reset_password)、expires_at、used_at、attempts
- `used_refresh_tokens`:jti(PK)、expires_at、used_at

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/auth/sms-code` | 匿名+限流 | `{phone, purpose, captcha_token?}` → 204;`captcha_enabled` 开启时 token 必填(缺失 400 `CAPTCHA_REQUIRED`、验签失败 400、渠道故障 502);mock 渠道固定码 123456 |
| `GET /api/v1/auth/captcha-config` | 匿名 | 验证码开关与 scene 配置 |
| `POST /api/v1/auth/register` | 匿名 | `{phone, sms_code, password?}` → 201 `{access_token, user}`(refresh 只走 HttpOnly Cookie);条款勾选前后端强校验 |
| `POST /api/v1/auth/login` | 匿名 | `{phone, sms_code \| password}`;冻结用户报 `USER_FROZEN` |
| `POST /api/v1/auth/password/reset` | 匿名 | 验证码重置密码 |
| `POST /api/v1/auth/refresh` | refresh cookie | 轮换发放新 token 对;不收 body,强制 `X-Requested-With: fetch` |
| `POST /api/v1/auth/logout` | 匿名(带 refresh cookie) | refresh 落 `used_refresh_tokens` 并清 Cookie;token 无效也回 204 |
| `POST /api/v1/auth/logout-all` | user | `token_version+1`,已签发 token 全失效 |
| `GET /api/v1/me` | user | 用户资料 |
| `PATCH /api/v1/me/warn-threshold` | user | 余额预警阈值 |
| `POST /api/v1/me/real-name` | user | 三要素实名(阿里云实人 `Mobile3MetaSimpleVerify`,BizCode 1 一致 / 2 不一致 / 3 无记录);`real_name_enabled` 关闭时 409 `REAL_NAME_DISABLED`;无 mock 渠道,测试经 `set_realname_provider` 注入 |
| `POST /api/v1/me/deletion-request` `GET` `POST .../cancel` | user | 账号注销:键入手机号确认 → 7 天冷静期(可撤销)→ 管理端执行 |
| `GET/POST/DELETE /api/v1/ssh-keys` | user | 公钥 CRUD |

## 规则与不变量

- 用户端与管理端 JWT audience 隔离(`SUPERDL_JWT_USER_AUDIENCE` / `SUPERDL_JWT_ADMIN_AUDIENCE`,默认 `superdl:user` / `superdl:admin`;issuer `SUPERDL_JWT_ISSUER` 默认 `superdl`);user token 访问管理端 API 一律 403。
- refresh token 一次性消费:jti 落 `used_refresh_tokens`,重放撤销该用户全部在外 token;同 jti 10s 内重复消费按正常轮换补发,不触发撤销。
- refresh token 只走 HttpOnly Cookie:prod 名 `__Host-superdl_refresh`(Secure + `path=/` + 无 Domain),非 prod `superdl_refresh`。
- `users.token_version` 是撤销闸;冻结用户同步递增。所有 `token_version` 读-改-写(改密/冻结/refresh 重放撤销/logout-all)带行锁(`with_for_update`)。
- 登录限流只计失败,四层桶:`user-login:{ip}:{phone}` 与 `user-login-acct:{phone}` 成功即清零,`user-login-ip:{ip}` 与 `user-login-acct-daily:{phone}` 不清零;注册、找回密码、实名核验与发码各有桶。计数落 PG(见 [security.md](./security.md)),限额见 [limits.md](./limits.md)。
- 密码规则只有一处 `core/security.PasswordStr`(≥12 字符、UTF-8 ≤72 字节,bcrypt 上限),用户端与管理端请求体共用,不合格 422。
- 验证码失败计次 `attempts` 达上限即作废(置 `used_at`)。同 phone 连续未消费的第 N 条发码间隔 `SUPERDL_SMS_SEND_INTERVAL_SECONDS` × 2^(N-1)(指数封顶 3),报 `SMS_TOO_FREQUENT`;手机号日配额按「验证码被消费」计。
- 短信发送失败必须作废已落库的验证码并返 502。
- 公钥须为 ssh-ed25519 / ssh-rsa / ecdsa-*;唯一性按 (user_id, fingerprint),重复报 `SSH_KEY_DUPLICATE`,非法报 `SSH_KEY_INVALID`;删除为硬删除。
- 身份证号只存脱敏值 + 带密钥摘要(`crypto.hash_id_number`,原文不落库);同摘要的非注销账号数达 `real_name_max_accounts_per_identity`(默认 3)即 409 `account.realNameIdentityLimit`。`real_name_enabled` 控制能否提交实名,`real_name_required_for_recharge` 控制充值/开通实例是否强制已实名。不变量:后者开启要求前者开启。
- 密码登录:四层桶预检 → 查用户 → **先提交只读事务还连接** → bcrypt(专属 4 线程池 `core/security._BCRYPT_EXECUTOR`);注册成功计 `superdl_user_signup_total`,发码成功计 `superdl_sms_sent_total{purpose}`。
- SSH 公钥:每用户 50 把、添加 20 次/小时(`MAX_SSH_KEYS_PER_USER`);创建实例按 id 集合下推 SQL 取键(`ssh_keys_by_ids`)。
- 注册必勾条款,同事务按当前 published 版落 terms/privacy 各一条同意存证。
- 注销执行为匿名化:手机号替换为随机不可逆令牌 `del:{user_id}:{16 位 hex}`(不取摘要,与原号无函数关系)、身份字段清空、全撤登录态;`balance_ledger` 与账单按法定义务保留。执行请求体必带操作原因 `note`,回写 `account_deletion_requests.note`(与驳回理由同列)。
- 所有写操作过审计中间件(actor/ip/result)。
