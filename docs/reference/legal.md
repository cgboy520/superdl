# 法务文档与同意存证

用户协议 / 隐私政策 / 注销须知的版本流(草稿 → 发布 → 归档)、公开读取与注册同意存证。模块 `app/modules/legal/`。

## 数据模型

- `legal_doc_versions`:doc_key(terms/privacy/deletion_notice,可扩展)、locale(zh-CN/en-US)、version、title、content_md、status(draft/published/archived)、effective_note?、created_by?/created_at、published_by?/published_at;UNIQUE(doc_key, locale, version);部分唯一索引保证每 (doc_key, locale) 至多一条 published
- `user_consents`:user_id、doc_key、version、accepted_at、client_ip —— 注册同意存证,追加式

## 契约

| 端点 | 角色/鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/legal/{doc_key}?lang=` | 匿名(IP 限流 120 次/分) | 当前 published 版;en-US 缺失回落 zh-CN 且 `fallback=true`;doc_key 非法或无 published 均 404 |
| `GET /api/admin/v1/legal-docs` | ops/finance/readonly(admin 恒可) | 总览:doc_key × locale 状态格(当前 published + 最新 draft,均空 = 缺失) |
| `GET /api/admin/v1/legal-docs/{doc_key}/versions?locale=` | 同上 | 版本历史(version 倒序) |
| `POST /api/admin/v1/legal-docs/{doc_key}/versions` | 仅 admin | `{locale}`:基于当前 published 复制出新 draft(version = max+1);该语言无 published 时以 zh-CN published 为底稿;每 (doc_key, locale) 同时只允许一个 draft(409) |
| `PUT /api/admin/v1/legal-docs/versions/{version_id}` | 仅 admin | 改 title / content_md / effective_note;非 draft 409;审计 detail 记版本与正文 sha256 |
| `POST /api/admin/v1/legal-docs/versions/{version_id}/publish` | 仅 admin | 同事务把同 (doc_key, locale) 旧 published 转 archived;审计记版本 + sha256 |
| `POST /api/admin/v1/legal-docs/versions/{version_id}/archive` | 仅 admin | draft → archived;published 不可直接归档(409) |

前端:用户端 `/legal/terms`、`/legal/privacy`、`/legal/deletion-notice` 渲染 published 正文(en-US 缺失回落 zh-CN);管理端在系统设置「法务文档」维护。

## 规则与不变量

- 预置内容:首个迁移把 terms / privacy / deletion_notice 的 zh-CN 正文作为 published v1 内联写入,迁移是唯一事实源;`service.VALID_DOC_KEYS` 只登记键面。单测走 create_all 不含迁移数据,`apps/api/tests/legal_preset.py` 保存同文快照供 conftest 播种。
- 注册必勾条款:注册成功同事务按当时 zh-CN published 版本落 terms 与 privacy 各一条 `user_consents`(含 client_ip);前后端都强校验勾选。
- 「每 (doc_key, locale) 至多一条 published」由部分唯一索引兜底并发发布;发布与归档都是行内状态迁移,不删行。
- 所有管理端写操作过审计中间件;正文变更以 sha256 留痕,不把全文写进审计。
- en-US 正文待法务出稿:工程侧版本流已就绪,缺稿时公开端点以 `fallback=true` 回落 zh-CN(`reference/i18n.md`)。
