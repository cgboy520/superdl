# WP20 · 平台配置中心(渠道凭据与合规,2026-08-19)

## 目标

微信支付 / 支付宝 / 阿里云短信 / 阿里云实名认证 / ICP 备案 五组配置在管理端在线化:
商户资质与凭据到位后运营自助录入即生效,不发版、不改 K8s Secret。
接口侧对齐官方现行规范:

- 微信支付 APIv3:支持**微信支付公钥模式**(`PUB_KEY_ID_*`,2024-10 后新商户唯一模式)
  与平台证书模式(存量商户),wechatpayv3 2.0.x 原生双模式。
- 支付宝:当面付 precreate + RSA2 普通公钥模式(alipay-sdk-python 官方 SDK,已有实现)。
- 阿里云短信:dysmsapi SendSms(RPC 签名,已有实现),凭据/签名/模板码配置化。
- 阿里云实名:实人认证 `Mobile3MetaSimpleVerify`(Cloudauth 2019-03-07,手机号三要素简版),
  BizCode 1 一致 / 2 不一致 / 3 无记录;新增真实 provider,mock 保留为 dev 默认。
- ICP 备案:备案号(+公安备案号)后端下发 `GET /api/v1/site-config`,页脚动态渲染,
  替代构建期 `VITE_ICP_NUMBER`(原部署链路未传 build-arg,线上恒显示"待配置")。

## 契约

- 机制:`app/core/platform_config.py`(仿 `policies.py`)—— `SETTING_SPECS` 白名单
  (未知键拒绝,防管理端提权),env 默认 + DB 覆盖,读路径一次轻查询。
- 敏感项(私钥/APIv3 密钥/AccessKeySecret)AES-256-GCM 加密落库(`app/core/crypto.py`),
  AAD 绑定键名;主密钥 `SUPERDL_CONFIG_ENCRYPTION_KEY` 只走 env,prod fail-fast 必配;
  读取接口只回配置状态与尾 4 位预览,永不回明文。
- 管理端 API(仅 `admin` 角色,凭据不下放 ops):
  - `GET /api/admin/v1/platform-config` → 分组配置项(来源 env/override、脱敏预览)
  - `PUT /api/admin/v1/platform-config` `{updates, reason}` → 校验(格式/枚举/prod 禁 mock)、
    空串=清除覆盖;审计 detail 只落键名与 reason,不落值
  - `POST /api/admin/v1/platform-config/test-sms` `{phone}` → 走当前生效渠道实发验证码
- 公开 API:`GET /api/v1/site-config` → `{icp_number, police_record_number, payment_channels}`;
  `GET /api/v1/policies` 的 `real_name_required_for_recharge` 改读平台配置。
- 工厂异步化(动态配置免重启):`get_channel(name, session)` / `get_sms_channel(session)` /
  `get_realname_provider(session)`;微信/支付宝渠道按配置指纹缓存实例(平台证书模式避免每次
  回调重复拉取平台证书);充值下单校验渠道开关(`payment_wechat_enabled`/`payment_alipay_enabled`)。
- 前端:admin 新增「平台配置」页(5 Tab,secret 留空不改、显示已配置尾 4 位,保存需 reason);
  web 页脚改 site-config 动态渲染,充值弹窗微信/支付宝 Tab 随渠道开关启用 + antd QRCode 展码。

## 数据变更

- 新表 `platform_settings`:`key`(PK) / `value`(Text,secret 为 `enc:v1:` 密文)/
  `updated_by` / `updated_at`;迁移 `wp20_platform_settings`。
- `Settings` 新增:`config_encryption_key`、`real_name_provider`、`real_name_access_key_id`、
  `real_name_access_key_secret`、`payment_wechat_enabled`、`payment_alipay_enabled`、
  `wechat_public_key`、`wechat_public_key_id`、`icp_number`、`police_record_number`。

## 安全边界(不迁移项)

`payment_mock`、`sms_provider≠mock`(prod)、JWT/DB/域名等基础设施配置仍只走 env 且保留
prod fail-fast;平台配置写入侧同样拒绝 prod 下 `sms_provider=mock`。K8s Secret 注入的 env
仍是默认值层,DB 覆盖仅用于运营自助与轮转。

## 验收

- 加密往返/密文前缀/错 AAD 拒解;未知键/越权角色/prod 写 mock 均 4xx;GET 永不回明文。
- 覆盖写入后:site-config、policies、渠道开关、短信模板即时跟随(免重启)。
- 阿里实名 provider:MockTransport 下签名参数快照 + BizCode 1/2/3 + Code≠200 上抛。
- 后端 pytest 全绿;ruff/pyright/import-linter/alembic check 全绿;前端四件套全绿。
