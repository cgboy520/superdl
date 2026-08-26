# 账户

手机号+短信验证码注册/登录、JWT 会话、SSH 公钥管理、实名认证入口。

## 数据模型

- `users`:phone 唯一、password_hash(可空,主走验证码)、status(active/frozen)、low_balance_warn_hours、token_version、verification_status、实名字段(id_name/id_number,可空)
- `ssh_keys`:user_id、name、public_key、fingerprint(SHA256,唯一)
- `sms_codes`:phone、code_hash(带密钥摘要,非明文)、purpose(register/login)、expires_at、used_at、attempts
- `used_refresh_tokens`:jti(PK)、expires_at、used_at

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/auth/sms-code` | 匿名+限流 | `{phone, purpose, captcha_token?}` → 204(安全策略 `captcha_enabled` 开启时 token 必填,缺失 400 `CAPTCHA_REQUIRED`、验签失败 400、渠道故障 502;关闭时不校验);mock 短信渠道固定码 123456 并落日志 |
| `POST /api/v1/auth/register` | 匿名 | `{phone, sms_code, password?}` → `{access_token, refresh_token, user}`;条款勾选前后端强校验 |
| `POST /api/v1/auth/login` | 匿名 | `{phone, sms_code \| password}`;冻结用户报 `USER_FROZEN` |
| `POST /api/v1/auth/password/reset` | 匿名 | 验证码重置密码 |
| `POST /api/v1/auth/refresh` | refresh token | 轮换发放新 token 对 |
| `POST /api/v1/auth/logout` | 匿名(带 refresh token) | 登出当前会话:refresh 落 `used_refresh_tokens`;token 无效也回 204 |
| `POST /api/v1/auth/logout-all` | user | 登出全部会话:`token_version+1`,已签发 token 即刻全失效 |
| `GET /api/v1/me` | user | 用户资料 |
| `PATCH /api/v1/me/warn-threshold` | user | 余额预警阈值 |
| `POST /api/v1/me/real-name` | user | 三要素实名 provider seam(阿里云实人 `Mobile3MetaSimpleVerify`,BizCode 1 一致 / 2 不一致 / 3 无记录;dev 默认 mock) |
| `POST /api/v1/me/deletion-request` `GET` `POST .../cancel` | user | 账号注销:键入手机号确认 → 7 天冷静期(期间可撤销)→ 管理端执行 |
| `GET/POST/DELETE /api/v1/ssh-keys` | user | 公钥 CRUD |

## 规则与不变量

- 用户端与管理端 JWT audience 隔离(`SUPERDL_JWT_USER_AUDIENCE` / `SUPERDL_JWT_ADMIN_AUDIENCE`,默认 `superdl:user` / `superdl:admin`;issuer `SUPERDL_JWT_ISSUER` 默认 `superdl`);user token 访问管理端 API 一律 403。
- refresh token 一次性消费:jti 落 `used_refresh_tokens`,重放视为泄露并撤销该用户全部在外 token。**宽限窗**:同 jti 在 10s 内被重复消费视为并发重试,按正常轮换补发新对,不触发撤销。
- `users.token_version` 是撤销闸,递增即令已签发 token 全部失效;冻结用户同步递增。所有 `token_version` 读-改-写(改密/冻结/refresh 重放撤销/logout-all)必须带行锁(`with_for_update`)。
- 登录限流只计失败,四层桶:`user-login:{ip}:{phone}` 5 次/5 分钟与 `user-login-acct:{phone}` 10 次/15 分钟(成功即清零),`user-login-ip:{ip}` 60 次/时与 `user-login-acct-daily:{phone}` 30 次/日(只计失败、不清零)。注册与找回密码各 5 次/5 分钟(IP+手机号),实名核验 5 次/时/用户。全部限额汇总见 [limits.md](./limits.md)。
- 密码字段在 schema 层按 **UTF-8 字节数 ≤72** 校验(bcrypt 上限,不能按字符数);管理端口令在服务层同标拦截。
- 验证码失败计次 `attempts` ≥5 即作废(置 `used_at`,持久化在 DB,不可只存进程内);同 phone 重复发码有递增退避(连续未消费第 N 条的间隔为基础间隔 ×2^(N-1),基础间隔 `SUPERDL_SMS_SEND_INTERVAL_SECONDS` 默认 60s、封顶 480s),报 `SMS_TOO_FREQUENT`。
- 发码限流:尝试按 IP(20/h);手机号 10 次/日配额按「验证码被消费」计,同号轰炸由递增退避兜底。计数落 PG,见 [security.md](./security.md)。
- 短信发送失败时必须作废已落库的验证码并返 502,不留下可用码。
- 公钥须为 ssh-ed25519 / ssh-rsa / ecdsa-*;唯一性按 (user_id, fingerprint),同用户指纹重复报 `SSH_KEY_DUPLICATE`,非法公钥报 `SSH_KEY_INVALID`;删除为硬删除,删后可重添。
- 身份证号只存脱敏值;充值是否强制实名由 `real_name_required_for_recharge` 开关控制。
- 注册必勾条款,同事务按当前 published 版落 terms/privacy 各一条同意存证。
- 注销执行为匿名化:手机号哈希化(释放唯一约束,原号可再注册)、身份字段清空、全撤登录态;`balance_ledger` 与账单按法定义务保留。
- 所有写操作过审计中间件(actor/ip/result)。
