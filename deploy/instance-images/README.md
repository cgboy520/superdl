# 实例镜像(平台镜像目录的构建源)

`images` 表里的 `image_ref` 指向的就是这里构建出的镜像。平台创建 Pod 时**不覆盖
command/args**(见 `app/core/k8s/real.py::_create_pod_sync`),完全依赖镜像自身的
entrypoint,每个平台镜像必须自行满足下面的契约。

## 平台镜像契约(不满足则实例不可用)

| 约定 | 要求 |
|---|---|
| Jupyter 监听 | `--ip=0.0.0.0`,端口 `8888`;只绑 localhost 则 Service/Ingress 打不通 |
| Jupyter 鉴权 | 读环境变量 `JUPYTER_TOKEN` 作为 token,**缺失必须启动失败**;加载 `superdl_jupyter_auth` 扩展:`/superdl-bootstrap` 一次性票据(单次、60s,HMAC 密钥=token 本体)核销后种第一方 cookie,token 不进 URL |
| Jupyter 进程 | 守护循环拉起(不用 exec 当 PID 1),连续秒退 5 次才放弃 |
| Jupyter Origin | 读环境变量 `JUPYTER_ALLOW_ORIGIN`(本实例域名)作为 `ServerApp.allow_origin`;**禁止写死 `'*'`** —— cookie 会话下等于放行跨站 WebSocket 在用户实例内执行代码 |
| SSH host key | 首次生成后持久化到实例盘(`/root/.ssh/host_keys`),`/etc/ssh` 下为符号链接;否则 Pod 重建即变指纹 |
| SSH 公钥 | 读环境变量 `AUTHORIZED_KEYS`(多行)写入 `~/.ssh/authorized_keys`,sshd 监听 `22`,仅密钥登录 |
| 工作目录 | 用户数据放 `/root`(实例盘挂载点);数据盘挂 `/root/data` |
| HOME 与运行目录 | `HOME=/root`,且 Jupyter 的 runtime/data/config 目录都落在 `/root` 下 —— 共享池 userns(`hostUsers:false`)下 `/home/xxx` 不可写 |
| 基础镜像 | 与 SKU 的 `cuda_max` 兼容的 CUDA 运行时 |

## 构建与推送

三个镜像共用本目录同一份 `Dockerfile`、`entrypoint.sh` 与 `superdl_jupyter_auth.py`
(各只有一份,没有需要人工保持一致的副本);差异只有基座(`--build-arg BASE_IMAGE`,
digest 钉在下面的构建命令里,浮动 tag 会在重建时静默换基座)与 miniconda 基座不带
JupyterLab 需补装(`--build-arg JUPYTERLAB_VERSION`)。构建上下文就是本目录:

```bash
cd deploy/instance-images

# pytorch:quay.io/jupyter/pytorch-notebook(cuda12,自带 conda + PyTorch + JupyterLab)
docker build -t <registry>/pytorch:2.9.0-cu128 \
  --build-arg BASE_IMAGE=quay.io/jupyter/pytorch-notebook:cuda12-latest@sha256:85ab930435b7afc06396e2949a4fe508d027a7980a319bec6a92f827578e5343 .
docker push <registry>/pytorch:2.9.0-cu128

# tensorflow:quay.io/jupyter/tensorflow-notebook(cuda,自带 conda + TensorFlow + JupyterLab)
docker build -t <registry>/tensorflow:2.21.0-cuda \
  --build-arg BASE_IMAGE=quay.io/jupyter/tensorflow-notebook:cuda-latest@sha256:fc4c5b03dfbaa5d358375810a7b721bfcbe65ba1983a6829bc09297d334f0d63 .
docker push <registry>/tensorflow:2.21.0-cuda

# miniconda:continuumio/miniconda3(干净 conda 基座,不预装 DL 框架,用户自建环境;
# 基座无 JupyterLab,构建时按 JUPYTERLAB_VERSION 补装——它是平台契约的入口)
docker build -t <registry>/miniconda:26.5.3 \
  --build-arg BASE_IMAGE=continuumio/miniconda3:26.5.3-1@sha256:1808b31ef43e9c521cde5884ba4df9ec26d8d503a314cea787590f8550358a63 \
  --build-arg JUPYTERLAB_VERSION=4.6.3 .
docker push <registry>/miniconda:26.5.3

# 之后在 管理端 · 镜像与预热 中登记该 image_ref,并按需开启预热
```

推送到托管镜像仓、在管理端登记与预热的 SOP 见 `deploy/cluster/runbooks/image-prewarm.md`(托管仓 + Spegel P2P 节点间分发;集群内自建 registry 已废弃)。
