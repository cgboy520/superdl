# 平台配置中心

渠道凭据与站点合规信息在管理端在线化:资质到位后运营自助录入即生效,不发版、不改 K8s Secret。机制在 `app/core/platform_config.py`。

## 数据模型

- `platform_settings`:`key`(PK)、`value`(Text,secret 为 `enc:v1:` 密文)、`updated_by`、`updated_at`

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/admin/v1/platform-config` | admin | 分组配置项(来源 env/override、脱敏预览) |
| `PUT /api/admin/v1/platform-config` | admin | `{updates, reason}`;校验格式/枚举/prod 禁 mock;空串 = 清除覆盖 |
| `POST /api/admin/v1/platform-config/test-sms` | admin | `{phone}`,走当前生效渠道实发验证码 |
| `GET /api/v1/site-config` | 匿名 | `{icp_number, police_record_number, company_name, company_address, company_phone, business_license_url, support_email, support_wechat, payment_channels}`:页脚 / 帮助页 / 充值弹窗动态渲染,留空即不展示 |

配置组(`SettingGroup`):payment_wechat / payment_alipay(渠道能力见 [payment.md](./payment.md))、sms、real_name、captcha、compliance(备案号 `icp_number` / `police_record_number` + 经营主体公示 `company_name` / `company_address` / `company_phone` / `business_license_url`,《电子商务法》第十五条,页脚展示)、support(客服联系方式 `support_email` / `support_wechat`,页脚与帮助页展示)、cluster(键面见 [nodes.md](./nodes.md))、observability(`grafana_url` 外链、`oncall_phone` 值班手机号,见 [observability.md](./observability.md))。每个键与 `Settings` 同名字段一一对应,env 即默认值层。

`Settings` 相关键:`config_encryption_key`、`real_name_provider`、`real_name_access_key_id`、`real_name_access_key_secret`、`payment_wechat_enabled`、`payment_alipay_enabled`、`wechat_public_key`、`wechat_public_key_id`、`icp_number`、`police_record_number`。

## 规则与不变量

- `SETTING_SPECS` 是白名单:未知键一律拒绝,防管理端提权。取值为 env 默认 + DB 覆盖,读路径一次轻查询。
- 生效配置有进程内缓存(`get_effective_platform_config`):失效签名为 `(行数, max(updated_at), env 默认值层指纹)`——写入侧 upsert 显式 bump `updated_at`,增删动行数;单行密文解密失败只让该键回落 env 默认并打 error,不拖垮整份配置。
- 敏感项(私钥/APIv3 密钥/AccessKeySecret)以 AES-256-GCM 加密落库(`app/core/crypto.py`),AAD 绑定行的键名。
- 因 AAD 绑定键名,直接改行键名会静默毁掉密文:改名必须走 `LEGACY_KEY_ALIASES` 回落 —— 旧行以旧 key 做 AAD 解密、写新键后删旧行。
- 主密钥 `SUPERDL_CONFIG_ENCRYPTION_KEY` 只走 env,prod 下 fail-fast 必配。
- 读取接口只回配置状态与尾 4 位预览,永不回明文;审计 detail 只落键名与 reason,不落值。
- 凭据不下放 ops:平台配置三端点仅 `admin` 角色;ops 生成注册命令时由服务端代读,永不见明文。
- 不入配置中心:`payment_mock`、prod 下 `sms_provider≠mock`、JWT/DB/域名等基础设施配置只走 env 且保留 prod fail-fast;平台配置写入侧同样拒绝 prod 下 `sms_provider=mock`,以及「`real_name_required_for_recharge=true` + `real_name_provider=mock`」组合(与 `Settings._validate_prod` 同口径 fail-closed)。
- K8s Secret 注入的 env 是默认值层,DB 覆盖仅用于运营自助与轮转。
- 渠道工厂异步取生效配置:`get_channel(name, session)` / `get_sms_channel(session)` / `get_realname_provider(session)`;微信与支付宝渠道实例按配置指纹缓存(平台证书模式下不重复拉取平台证书)。
- 备案号由 `site-config` 运行期下发,页脚动态渲染,不进构建期 env。
