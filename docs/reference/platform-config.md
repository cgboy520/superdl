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
| `GET /api/v1/site-config` | 匿名 | `{icp_number, police_record_number, payment_channels}` |

配置组:payment(渠道能力见 [payment.md](./payment.md))、sms 与 real-name、icp、cluster(键面见 [nodes.md](./nodes.md))、observability(见 [observability.md](./observability.md))。

`Settings` 相关键:`config_encryption_key`、`real_name_provider`、`real_name_access_key_id`、`real_name_access_key_secret`、`payment_wechat_enabled`、`payment_alipay_enabled`、`wechat_public_key`、`wechat_public_key_id`、`icp_number`、`police_record_number`。

## 规则与不变量

- `SETTING_SPECS` 是白名单:未知键一律拒绝,防管理端提权。取值为 env 默认 + DB 覆盖,读路径一次轻查询。
- 敏感项(私钥/APIv3 密钥/AccessKeySecret)以 AES-256-GCM 加密落库(`app/core/crypto.py`),AAD 绑定行的键名。
- 因 AAD 绑定键名,直接改行键名会静默毁掉密文:改名必须走 `LEGACY_KEY_ALIASES` 回落 —— 旧行以旧 key 做 AAD 解密、写新键后删旧行。
- 主密钥 `SUPERDL_CONFIG_ENCRYPTION_KEY` 只走 env,prod 下 fail-fast 必配。
- 读取接口只回配置状态与尾 4 位预览,永不回明文;审计 detail 只落键名与 reason,不落值。
- 凭据不下放 ops:平台配置三端点仅 `admin` 角色;ops 生成注册命令时由服务端代读,永不见明文。
- 不入配置中心:`payment_mock`、prod 下 `sms_provider≠mock`、JWT/DB/域名等基础设施配置只走 env 且保留 prod fail-fast;平台配置写入侧同样拒绝 prod 下 `sms_provider=mock`。
- K8s Secret 注入的 env 是默认值层,DB 覆盖仅用于运营自助与轮转。
- 渠道工厂异步化以免重启:`get_channel(name, session)` / `get_sms_channel(session)` / `get_realname_provider(session)`;微信与支付宝渠道实例按配置指纹缓存,平台证书模式避免每次回调重复拉取平台证书。
- 备案号由 `site-config` 运行期下发,页脚动态渲染,不依赖构建期 env。
