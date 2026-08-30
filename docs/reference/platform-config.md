# 平台配置中心

渠道凭据与站点合规信息在管理端在线录入即生效,不发版、不改 K8s Secret。机制在 `app/core/platform_config.py`。

## 数据模型

- `platform_settings`:`key`(PK)、`value`(Text,secret 为 `enc:v1:` 密文)、`updated_by`、`updated_at`

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/admin/v1/platform-config` | admin | 分组配置项(来源 env/override、脱敏预览)+ `warnings`(服务端计算的配置风险 `{key, level: error|warning, message}`,与 prod lifespan 启动日志共用 `compute_config_warnings`:prod 关人机验证 / 关管理端两步验证、开人机验证或实名而凭据不全) |
| `PUT /api/admin/v1/platform-config` | admin | `{updates, reason}`;校验格式/枚举/prod 禁 mock;空串 = 清除覆盖 |
| `POST /api/admin/v1/platform-config/test-sms` | admin | `{phone}`,走当前生效渠道实发验证码 |
| `POST /api/admin/v1/platform-config/test-registry` | admin | 无体;按生效 `registry_*` 探测 Harbor:`GET /api/v2.0/health`(DNS/TLS/CA)→ 机器人鉴权 `GET /projects/{project}/repositories?page_size=1`(401 凭据错 / 403 无 List 权限 / 404 项目不存在)→ `{ok, step, detail, harbor_version, repositories}`;限流 10/h,审计只落 host |
| `GET /api/v1/site-config` | 匿名 | `{icp_number, police_record_number, company_name, company_address, company_phone, business_license_url, support_email, support_wechat, payment_channels}`:页脚 / 帮助页 / 充值弹窗动态渲染,留空即不展示 |

配置组(`SettingGroup`):security(安全策略开关 `captcha_enabled` / `admin_mfa_enabled` / `real_name_enabled` / `real_name_required_for_recharge`:开关 ≠ 替身,关闭即跳过对应校验,凭据仍在各渠道组;prod 在线关闭一律禁(`prod_forbidden`),人机验证/实名再叠加 prod 启动 fail-fast)、payment_wechat / payment_alipay(渠道能力见 [payment.md](./payment.md))、sms、real_name、captcha、compliance(备案号 `icp_number` / `police_record_number` + 经营主体公示 `company_name` / `company_address` / `company_phone` / `business_license_url`,《电子商务法》第十五条,页脚展示)、support(客服联系方式 `support_email` / `support_wechat`,页脚与帮助页展示)、cluster(键面见 [nodes.md](./nodes.md))、registry(镜像仓库 Harbor:`registry_host` / `registry_project`(默认 superdl)/ `registry_robot_name` / `registry_robot_secret`(secret)/ `registry_ca_pem` / `registry_proxy_projects`(每行 `上游=代理项目`)/ `image_allowed_registries`(每行一个仓库前缀,空 = 不限制,Harbor 地址自动放行;见 [images.md](./images.md) 与 [security.md](./security.md)))、observability(`grafana_url` 外链、`oncall_phone` 值班手机号,见 [observability.md](./observability.md))。每个键与 `Settings` 同名字段一一对应,env 即默认值层。

## 规则与不变量

- `SETTING_SPECS` 是白名单:未知键一律拒绝,防管理端提权。取值为 env 默认 + DB 覆盖。
- 生效配置不做进程内缓存(`get_effective_platform_config`):每次一趟全量 SELECT + 解密,写入即生效;单行密文解密失败 fail-closed 抛错(禁止静默回落 env——轮换窗口里回落等于悄悄用回旧值)。
- 敏感项(私钥/APIv3 密钥/AccessKeySecret)以 AES-256-GCM 加密落库(`app/core/crypto.py`),AAD 绑定行的键名;密文带 kid(主密钥指纹),加密用钥经 HKDF 从主密钥派生。
- **因 AAD 绑定键名,直接 UPDATE 行键名会静默毁掉密文**:secret 键改名须由迁移按旧 key 解密后以新 key 重加密写入(或让运营重新录入),不做读侧别名回落。
- 主密钥 `SUPERDL_CONFIG_ENCRYPTION_KEY` 只走 env,prod 下 fail-fast 必配;轮换经 `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` 双密钥读迁移(见 [../../deploy/cluster/runbooks/key-rotation.md](../../deploy/cluster/runbooks/key-rotation.md))。
- 读取接口只回配置状态与尾 4 位预览,永不回明文;审计 detail 只落键名与 reason,不落值。
- 凭据不下放 ops:平台配置三端点仅 `admin` 角色;ops 生成注册命令时由服务端代读,永不见明文。
- 不入配置中心:`payment_mock`、prod 下 `sms_provider≠mock`、JWT/DB/域名等基础设施配置只走 env 且保留 prod fail-fast;写入侧同样拒绝 prod 下 `sms_provider=mock`(`prod_forbidden`)。另有环境无关的不变量 `real_name_required_for_recharge=true ⇒ real_name_enabled=true`(`_check_real_name_invariant`,与 `Settings._validate_invariants` 同口径)。
- K8s Secret 注入的 env 是默认值层,DB 覆盖仅用于运营自助与轮转。
- 渠道工厂异步取生效配置:`get_channel(name, session)` / `get_sms_channel(session)` / `get_realname_provider(session)`;微信与支付宝渠道实例按配置指纹缓存(平台证书模式下不重复拉取平台证书)。
- 备案号由 `site-config` 运行期下发,页脚动态渲染,不进构建期 env。
