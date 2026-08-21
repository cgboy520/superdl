# 实例镜像(平台镜像目录的构建源)

`images` 表里的 `image_ref` 指向的就是这里构建出的镜像。平台创建 Pod 时**不覆盖
command/args**(见 `app/core/k8s/real.py::_create_pod_sync`),完全依赖镜像自身的
entrypoint,因此每个平台镜像必须自行满足下面的契约。

## 平台镜像契约(不满足则实例不可用)

| 约定 | 要求 | 不满足的后果 |
|---|---|---|
| Jupyter 监听 | 必须 `--ip=0.0.0.0`,端口 `8888` | 只绑 localhost 时 Service/Ingress 全部打不通,用户端 JupyterLab 入口 502/连接中断 |
| Jupyter 鉴权 | 读环境变量 `JUPYTER_TOKEN` 作为 token | 用户端给出的 `?token=` 链接无法登录 |
| Jupyter Origin | 读环境变量 `JUPYTER_ALLOW_ORIGIN`(本实例域名)作为 `ServerApp.allow_origin`;**禁止写死 `'*'`** | token 登录后走 cookie 会话,放开 Origin 等于允许恶意网页发起带 cookie 的跨站 WebSocket,在用户实例内执行代码 |
| SSH 公钥 | 读环境变量 `AUTHORIZED_KEYS`(多行)写入 `~/.ssh/authorized_keys`,sshd 监听 `22`,仅密钥登录 | SSH 入口不可用 |
| 工作目录 | 用户数据放 `/root`(实例盘挂载点);数据盘挂 `/root/data` | 关机后数据丢失 |
| HOME 与运行目录 | `HOME=/root`,且 Jupyter 的 runtime/data/config 目录都落在 `/root` 下 | 基础镜像若默认 `HOME=/home/xxx`,共享池的 userns(`hostUsers:false`)下该目录不可写,Jupyter 启动即 `PermissionError` 退出 |
| 基础镜像 | 与 SKU 的 `cuda_max` 兼容的 CUDA 运行时 | CUDA 程序无法运行 |

## 构建与推送

```bash
cd deploy/instance-images/pytorch
docker build -t <registry>/pytorch:2.9.0-cu128 .
docker push <registry>/pytorch:2.9.0-cu128
# 之后在 管理端 · 镜像与预热 中登记该 image_ref,并按需开启预热
```

生产 registry 见 `deploy/cluster/registry/`(集群内 registry + Spegel P2P 分发)。
