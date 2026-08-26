# 实例镜像(平台镜像目录的构建源)

`images` 表里的 `image_ref` 指向的就是这里构建出的镜像。平台创建 Pod 时**不覆盖
command/args**(见 `app/core/k8s/real.py::_create_pod_sync`),完全依赖镜像自身的
entrypoint,每个平台镜像必须自行满足下面的契约。

## 平台镜像契约(不满足则实例不可用)

| 约定 | 要求 |
|---|---|
| Jupyter 监听 | `--ip=0.0.0.0`,端口 `8888`;只绑 localhost 则 Service/Ingress 打不通 |
| Jupyter 鉴权 | 读环境变量 `JUPYTER_TOKEN` 作为 token,**缺失必须启动失败**;加载 `superdl_jupyter_auth` 扩展:`/superdl-bootstrap` 一次性票据(单次、60s,HMAC 密钥=token 本体)核销后种第一方 cookie,token 不进 URL |
| Jupyter 默认界面 | `--ServerApp.default_url=/lab`,票据核销后 302 到 `/lab`;界面语言默认 zh-CN(`lab-overrides.json`,用户可在设置里改) |
| Jupyter 进程 | 守护循环拉起(不用 exec 当 PID 1),连续秒退 5 次才放弃 |
| Jupyter Origin | 读环境变量 `JUPYTER_ALLOW_ORIGIN`(本实例域名)作为 `ServerApp.allow_origin`;**禁止写死 `'*'`** —— cookie 会话下等于放行跨站 WebSocket 在用户实例内执行代码 |
| Jupyter 套件 | 每个镜像必装:`jupyterlab` / `jupyter-ai` / `jupyter-resource-usage` / `jupyterlab-language-pack-zh-CN` / `ipykernel`(少了 ipykernel 实例里没有 Python 内核) |
| SSH host key | 首次生成后持久化到实例盘(`/root/.ssh/host_keys`),`/etc/ssh` 下为符号链接;否则 Pod 重建即变指纹 |
| SSH 公钥 | 读环境变量 `AUTHORIZED_KEYS`(多行)写入 `~/.ssh/authorized_keys`,sshd 监听 `22`,仅密钥登录 |
| 工作目录 | 用户数据放 `/root`(实例盘挂载点);数据盘挂 `/root/data` |
| HOME 与运行目录 | `HOME=/root`,且 Jupyter 的 runtime/data/config 目录都落在 `/root` 下 —— 共享池 userns(`hostUsers:false`)下 `/home/xxx` 不可写 |
| 基础镜像 | 与 SKU 的 `cuda_max` 兼容的 CUDA 运行时 |

## 默认镜像矩阵(平台自带目录)

选版规则(新版本上架照此推演,别逐个拍脑袋):

- **框架版本**:只上「最新稳定版」+「最后一个支持 CUDA 11.8 的稳定版」;最新版本自己就覆盖 11.8 时只留一个。不收 rc/beta。
- **CUDA 线**:`13.2` / `12.9` / `11.8` 三条。取值以**框架官方轮子实际发布的 CUDA**为准,基座 `nvidia/cuda` 的小版本与之对齐(nvcc 与 wheel 同线,用户编译扩展不打架)。CUDA 13.3 存在但 PyTorch 最新只发到 `cu132`,故 13 线钉 13.2。
- **Python**:取该框架支持的**最高**版本(能上 3.13 就 3.13);封顶了才降(TF 2.14 只到 cp311 → 3.11),PaddlePaddle 用厂商基座自带的 3.10。
- **框架镜像以同线 Miniconda 镜像为父镜像**:conda / sshd / Jupyter 套件是同一批 layer,Harbor 与节点上只存一份,推送时只上传框架 wheel 那一层。

| 镜像 tag | 框架 | Python | CUDA | 基座 |
|---|---|---|---|---|
| `miniconda:26.5.3-cu132-py313` | —(干净 conda) | 3.13 | 13.2 | `nvidia/cuda:13.2.1-cudnn-devel-ubuntu24.04` |
| `pytorch:2.13.0-cu132-py313` | PyTorch 2.13.0 | 3.13 | 13.2 | ↑ 同线 miniconda |
| `miniconda:26.5.3-cu129-py313` | —(干净 conda) | 3.13 | 12.9 | `nvidia/cuda:12.9.2-cudnn-devel-ubuntu24.04` |
| `pytorch:2.13.0-cu129-py313` | PyTorch 2.13.0 | 3.13 | 12.9 | ↑ 同线 miniconda |
| `tensorflow:2.21.0-cu129-py313` | TensorFlow 2.21.0 | 3.13 | 12.9 | ↑ 同线 miniconda |
| `miniconda:26.5.3-cu118-py313` | —(干净 conda) | 3.13 | 11.8 | `nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04` |
| `pytorch:2.7.1-cu118-py313` | PyTorch 2.7.1 | 3.13 | 11.8 | ↑ 同线 miniconda |
| `tensorflow:2.14.1-cu118-py311` | TensorFlow 2.14.1 | 3.11 | 11.8 | CUDA 11.8 基座 + py311 conda(TF 2.14 无 cp313 轮子,不能挂在 py313 父镜像上) |
| `paddle:3.3.1-cu130-py310` | PaddlePaddle 3.3.1 | 3.10 | 13.0 | `paddlepaddle/paddle:3.3.1-gpu-cuda13.0-cudnn9.13` |
| `paddle:3.3.1-cu129-py310` | PaddlePaddle 3.3.1 | 3.10 | 12.9 | `paddlepaddle/paddle:3.3.1-gpu-cuda12.9-cudnn9.9` |
| `paddle:3.3.1-cu118-py310` | PaddlePaddle 3.3.1 | 3.10 | 11.8 | `paddlepaddle/paddle:3.3.1-gpu-cuda11.8-cudnn8.9` |

已知空档(不是漏了):

- **TensorFlow 无 13.x**:TF 2.21 的 CUDA 依赖钉在 `>=12.5,<13.0`,官方尚未出 CUDA 13 轮子。
- **PyTorch 无 torchaudio(2.13.0 两个镜像)**:torchaudio 停在 2.11.0,与 torch 2.13 不配套,装它会把 torch 拽回 2.11;2.7.1 镜像三件套齐全。
- **PaddlePaddle 的 CUDA 线是厂商定的**(13.0 / 12.9 / 11.8),不与上面三条线完全重合;这三个镜像直接用飞桨官方基座补平台契约层,不自建。

## 构建与推送

一份 `Dockerfile` + `entrypoint.sh` + `superdl_jupyter_auth.py` + `lab-overrides.json`,三种用法靠 build-arg 区分
(文件头注释有全表)。构建上下文就是本目录。基座一律钉 digest:浮动 tag 会在重建时静默换基座。

```bash
cd deploy/instance-images
REG=<registry>/superdl
JUP="jupyterlab==4.6.3 jupyter-ai==3.1.3 jupyter-resource-usage==1.3.0 jupyterlab-language-pack-zh-CN==4.5.post3 ipykernel==7.3.0"

# ---- CUDA 13.2 线 ----
docker build -t $REG/miniconda:26.5.3-cu132-py313 \
  --build-arg BASE_IMAGE=nvidia/cuda:13.2.1-cudnn-devel-ubuntu24.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py313_26.5.3-2-Linux-x86_64.sh \
  --build-arg JUPYTER_PACKAGES="$JUP" .
docker build -t $REG/pytorch:2.13.0-cu132-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu132-py313 \
  --build-arg FRAMEWORK_PIP="torch==2.13.0+cu132 torchvision==0.28.0+cu132" \
  --build-arg FRAMEWORK_PIP_INDEX=https://download.pytorch.org/whl/cu132 .

# ---- CUDA 12.9 线 ----
docker build -t $REG/miniconda:26.5.3-cu129-py313 \
  --build-arg BASE_IMAGE=nvidia/cuda:12.9.2-cudnn-devel-ubuntu24.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py313_26.5.3-2-Linux-x86_64.sh \
  --build-arg JUPYTER_PACKAGES="$JUP" .
docker build -t $REG/pytorch:2.13.0-cu129-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu129-py313 \
  --build-arg FRAMEWORK_PIP="torch==2.13.0+cu129 torchvision==0.28.0+cu129" \
  --build-arg FRAMEWORK_PIP_INDEX=https://download.pytorch.org/whl/cu129 .
docker build -t $REG/tensorflow:2.21.0-cu129-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu129-py313 \
  --build-arg FRAMEWORK_PIP="tensorflow[and-cuda]==2.21.0" .

# ---- CUDA 11.8 线 ----
docker build -t $REG/miniconda:26.5.3-cu118-py313 \
  --build-arg BASE_IMAGE=nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py313_26.5.3-2-Linux-x86_64.sh \
  --build-arg JUPYTER_PACKAGES="$JUP" .
docker build -t $REG/pytorch:2.7.1-cu118-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu118-py313 \
  --build-arg FRAMEWORK_PIP="torch==2.7.1+cu118 torchvision==0.22.1+cu118 torchaudio==2.7.1+cu118" \
  --build-arg FRAMEWORK_PIP_INDEX=https://download.pytorch.org/whl/cu118 .
docker build -t $REG/tensorflow:2.14.1-cu118-py311 \
  --build-arg BASE_IMAGE=nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py311_26.5.3-2-Linux-x86_64.sh \
  --build-arg JUPYTER_PACKAGES="$JUP" \
  --build-arg FRAMEWORK_PIP="tensorflow[and-cuda]==2.14.1" .

# ---- PaddlePaddle(厂商基座,只补平台契约层;基座自带 python3.10 与 paddle)----
PADDLE=ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddle
for pair in "cu130:3.3.1-gpu-cuda13.0-cudnn9.13" "cu129:3.3.1-gpu-cuda12.9-cudnn9.9" "cu118:3.3.1-gpu-cuda11.8-cudnn8.9"; do
  docker build -t "$REG/paddle:3.3.1-${pair%%:*}-py310" \
    --build-arg BASE_IMAGE="$PADDLE:${pair#*:}" --build-arg JUPYTER_PACKAGES="$JUP" .
done
```

推送前自检(三步,缺一不可):

```bash
IMG=$REG/pytorch:2.13.0-cu132-py313
# ① 扩展能在目标基座的 jupyter_server 上 import
docker run --rm --entrypoint python $IMG -c "import sys; sys.path.insert(0,'/opt/superdl'); import superdl_jupyter_auth; print('ok')"
# ② Jupyter 套件齐全(4 个模块 + 内核)
docker run --rm --entrypoint bash $IMG -lc 'python -c "import jupyter_ai, jupyter_resource_usage, ipykernel"; jupyter labextension list 2>&1 | grep -ci "language-pack\|jupyter-ai\|resource-usage"'
# ③ 用镜像自己的 entrypoint 起一次,入场 URL 对坏票据回 403(404 = 扩展没加载;容器秒退 = 启动参数错)
docker run -d --name jcheck -e JUPYTER_TOKEN=selfcheck -p 127.0.0.1:18888:8888 $IMG && sleep 20 \
  && curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:18888/superdl-bootstrap?code=x&exp=1&sig=y'   # 期望 403
docker rm -f jcheck
docker push $IMG
```

GPU 可用性只能在有卡的节点上验(本机构建机无卡):推送并在管理端登记后,建一台实例跑
`python -c "import torch;print(torch.cuda.is_available())"` / `tf.config.list_physical_devices('GPU')` / `paddle.utils.run_check()`。

之后在 管理端 · 镜像与预热 中登记 image_ref,并按需开启预热。**tag 不可变**:改了本目录任何文件都换新 tag
重推(节点 `imagePullPolicy=IfNotPresent`,同名 tag 不会重拉),再在管理端改 image_ref。

推送到托管镜像仓、在管理端登记与预热的 SOP 见 `deploy/cluster/runbooks/image-prewarm.md`(托管仓 + Spegel P2P 节点间分发;集群内自建 registry 已退役)。
