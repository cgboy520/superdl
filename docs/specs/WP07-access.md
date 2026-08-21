# WP7 · 接入

## 目标
SSH 端口池 + JupyterLab 泛域名 Ingress + token。

## 设计
- SSH:`port_allocations` 池(30000~32767);实例创建时分配,释放回池;展示 `ssh root@ssh1.<域名> -p 3xxxx`;仅密钥登录(公钥注入 authorized_keys),禁密码
- JupyterLab:实例 Pod 内跑 JupyterLab;`<instance-uuid>.app.<域名>` Ingress 按 host 路由到实例 Service;token 控制面生成注入(env);`GET /instances/{id}/access` 返回 SSH 指令 + Jupyter URL(含 token);token 重置接口
- K8s 对象(Service NodePort / Ingress)由 orchestrator 统一产出(FakeOrchestrator 断言 spec);泛域名证书等集群侧实配见 deploy/cluster/

## 验收
- access 接口返回可用的 SSH 指令与 Jupyter URL;非 running 时报错并说明
- token 重置后旧 URL 失效(重建 Pod env 或 secret 更新)
- 端口池耗尽时创建失败,给出明确错误;释放后端口可复用
