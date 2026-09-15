# 平台配置中心

渠道凭据、安全开关、站点合规信息、集群接入与运营策略参数在管理端在线录入即生效。机制在 `app/core/platform_config.py`:一份 `SETTING_SPECS` 白名单、一张 `platform_settings` 表、一个强类型读取面 `RuntimeConfig`。

## 数据模型

- `platform_settings`:`key`(PK)、`value`(Text,secret 为 `enc:v2:` 密文)、`updated_by`、`updated_at`

## 契约

| 端点                                               | 角色/鉴权                      | 说明                                                                                                                                                                                                                                               |
| -------------------------------------------------- | ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/admin/v1/platform-config`                | admin                          | 分组配置项(不含 policy 组;来源 env/override、脱敏预览)+ `warnings`(`{key, level: error                                                                                                                                                             | warning, message}`,与 prod lifespan 启动日志共用 `compute_config_warnings`) |
| `PUT /api/admin/v1/platform-config`                | admin                          | `{updates, reason}`;校验格式/枚举/prod 禁 mock;空串 = 清除覆盖;policy 组的键一律 400                                                                                                                                                               |
| `GET/PUT /api/admin/v1/policies`                   | 读 ops/finance/readonly,写 ops | policy 组的切片:生效值 + 取值区间(`lo`/`hi`)+ 覆盖项;只收 policy 组的键,见 [limits.md](./limits.md)                                                                                                                                                |
| `POST /api/admin/v1/platform-config/test-sms`      | admin                          | `{phone}`,走当前生效渠道实发验证码                                                                                                                                                                                                                 |
| `POST /api/admin/v1/platform-config/test-registry` | admin                          | 按生效 `registry_*` 探测 Harbor:`GET /api/v2.0/health` → 机器人鉴权 `GET /projects/{project}/repositories?page_size=1`(401 凭据错 / 403 无 List 权限 / 404 项目不存在)→ `{ok, step, detail, harbor_version, repositories}`;限流 10/h,审计只落 host |
| `GET /api/v1/site-config`                          | 匿名                           | `{icp_number, police_record_number, company_name, company_address, company_phone, business_license_url, support_email, support_wechat, payment_channels}`,留空即不展示                                                                             |

配置组(`SettingGroup`):security(`captcha_enabled` / `admin_mfa_enabled` / `real_name_enabled` / `real_name_required_for_recharge`:关闭即跳过对应校验,凭据仍在各渠道组;prod 在线关闭一律禁 `prod_forbidden`,人机验证/实名再叠加 prod 启动 fail-fast)、payment_wechat / payment_alipay(见 [payment.md](./payment.md))、sms、real_name、captcha、compliance(`icp_number` / `police_record_number` / `company_name` / `company_address` / `company_phone` / `business_license_url`,页脚展示)、support(`support_email` / `support_wechat`)、cluster(键面见 [nodes.md](./nodes.md))、registry(`registry_host` / `registry_project`(默认 superdl)/ `registry_robot_name` / `registry_robot_secret`(secret)/ `registry_ca_pem` / `registry_proxy_projects`(每行 `上游=代理项目`)/ `image_allowed_registries`(每行一个仓库前缀,生效时一律补成以 `/` 结尾;空 = 不限制,Harbor 地址自动放行;见 [images.md](./images.md) 与 [security.md](./security.md)))、observability(`grafana_url`、`oncall_phone`,见 [observability.md](./observability.md))、policy(运营策略参数:盘价 / 盘容量与宽限 / 冻结时长 / 余额覆盖小时 / 预热覆盖率 / 每用户配额 / GPU 节点 CPU 实例上限 / 包周期折扣与预警 / 竞价折扣与宽限,kind 为 `int` / `decimal`,spec 带 `lo`/`hi` 区间)。每个键与 `Settings` 同名字段一一对应,env 即默认值层;`RuntimeConfig` 字段与 `SETTING_SPECS` 一一对应、类型由 kind 决定(bool / int / Decimal / 其余 str),三者一致由 `tests/test_platform_config.py` 锁定。

## 规则与不变量

- `SETTING_SPECS` 是白名单:未知键一律拒绝;写入口按配置组隔离(`/policies` 只许 policy 组,`/platform-config` 不许 policy 组)。取值为 env 默认 + DB 覆盖;env 层非空值在启动时同样过格式校验(`env_layer_problems`,prod 不合格拒启)。每次写入按配置组计数 `superdl_platform_config_write_total{domain}`,payment / crypto 组条条告警 `PaymentConfigWritten`。
- 生效配置不做进程内缓存(`get_runtime_config` → `RuntimeConfig`;管理端展示用字符串映射 `effective_strings`):每次全量 SELECT + 解密,写入即生效;单行密文解密失败 fail-closed 抛错,禁止静默回落 env。业务代码只经 `RuntimeConfig` 的类型化字段取值,不比较字符串 `"true"`。
- prod 禁止取值(`SettingSpec.prod_forbidden`)是单一事实源:在线写入与清除覆盖拒绝、配置页红牌(`compute_config_warnings`,`prod_gate=True` 的为 error、其余 warning)、启动合规闸(`assert_prod_compliance_gates`,只看 `prod_gate=True` 的键)全部从它派生。
- 敏感项以 AES-256-GCM 加密落库(`app/core/crypto.py`),AAD 绑定行的键名;密文带 kid,加密用钥经 HKDF 从主密钥派生。
- **AAD 绑定键名,直接 UPDATE 行键名会毁掉密文**:secret 键改名须由迁移按旧 key 解密后以新 key 重加密写入(或让运营重录)。
- 主密钥 `SUPERDL_CONFIG_ENCRYPTION_KEY` 只走 env,prod fail-fast 必配;轮换经 `SUPERDL_CONFIG_ENCRYPTION_KEY_PREVIOUS` 双密钥读迁移(见 [../../deploy/cluster/runbooks/key-rotation.md](../../deploy/cluster/runbooks/key-rotation.md))。
- 读取接口只回配置状态与尾 4 位预览;审计 detail 只落键名与 reason。
- 凭据不下放 ops:平台配置三端点仅 `admin`;ops 生成注册命令时由服务端代读。
- 不入配置中心:`payment_mock`、prod 下 `sms_provider≠mock`、JWT/DB/域名等基础设施配置只走 env 且保留 prod fail-fast;写入侧拒绝 prod 下 `sms_provider=mock`(`prod_forbidden`)。环境无关不变量 `real_name_required_for_recharge=true ⇒ real_name_enabled=true`(`_check_real_name_invariant`,与 `Settings._validate_invariants` 同口径)。
- 渠道工厂异步取生效配置:`get_channel(name, session)` / `get_sms_channel(session)` / `get_realname_provider(session)`;微信与支付宝渠道实例按配置指纹缓存。
- 备案号由 `site-config` 运行期下发,不进构建期 env。
