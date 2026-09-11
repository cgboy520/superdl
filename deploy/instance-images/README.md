# 实例镜像(平台镜像目录的构建源)

`images` 表里的 `image_ref` 指向这里构建出的镜像。平台创建 Pod 时**不覆盖 command/args**(见 `app/core/k8s/real.py::_create_pod_sync`),每个平台镜像必须自行满足下面的契约。

## 服务型容器不受本契约约束

下面的契约管**开发机形态**(`workload_type='dev'`,SSH + JupyterLab)的平台镜像。
服务型实例(`workload_type='service'`,见 [../../docs/reference/services.md](../../docs/reference/services.md))跑用户自己的镜像,平台只要求:在用户声明的 `service_port` 上监听 HTTP,且监听 `0.0.0.0`。

服务型容器**不需要**:内置 sshd、内置 JupyterLab、`JUPYTER_TOKEN` / `AUTHORIZED_KEYS` 环境变量、`superdl_jupyter_auth` 扩展、Jupyter 套件、自己实现 API Key 鉴权(网关 `SecurityPolicy.extAuth` 负责)。
可选两项:声明了 `health_path` 时在该路径返回 2xx(同时是 startupProbe 与 readinessProbe);勾了「同时开放 SSH」时需要 sshd,下面 SSH 相关条目重新适用。

容器可信任的平台注入头只有 `x-superdl-endpoint` 与 `x-superdl-key-id`(网关侧 `headersToBackend` 白名单);**其余同名头都可能是客户端伪造的**。

## 平台镜像契约(不满足则实例不可用)

| 约定 | 要求 |
|---|---|
| Jupyter 监听 | `--ip=0.0.0.0`,端口 `8888` |
| Jupyter 鉴权 | 读环境变量 `JUPYTER_TOKEN` 作为 token,**缺失必须启动失败**;**必须显式传 `--IdentityProvider.token="$JUPYTER_TOKEN"`**(命令行优先级高于配置文件)。加载 `superdl_jupyter_auth` 扩展:`/superdl-bootstrap` 一次性票据(单次、60s,HMAC 密钥=token 本体)核销后种第一方 cookie,token 不进 URL |
| Jupyter 默认界面 | `--ServerApp.default_url=/lab`,票据核销后 302 到 `/lab`;界面语言默认 zh-CN(`lab-overrides.json`,用户可在设置里改) |
| Jupyter 终端 | entrypoint 导出 `SHELL=/bin/bash`,非 tty 下追加 `-l` 走登录 shell,读 `/etc/profile.d/superdl-env.sh`;基座必须带 bash |
| Jupyter 进程 | 守护循环拉起(不用 exec 当 PID 1),连续秒退 5 次才放弃 |
| Jupyter Origin | 读环境变量 `JUPYTER_ALLOW_ORIGIN`(本实例域名)作为 `ServerApp.allow_origin`;**禁止写死 `'*'`** |
| Jupyter 套件 | 每个镜像必装:`jupyterlab` / `jupyter-ai[jupyternaut,magics]` / `jupyter-resource-usage` / `jupyterlab-language-pack-zh-CN` / `ipykernel` |
| jupyter-ai | 必须带 `[jupyternaut,magics]` extra。entrypoint 另下发:模型提供方白名单(`openai` / `anthropic` / `github_copilot` / `ollama` / `ollama_chat`)与显式默认 persona(`::jupyter_ai_jupyternaut::`) |
| 补充组命名 | entrypoint 为运行时注入的宿主 video/render 补充组补 `hostgrp<gid>` 记录,并把 `root` 写进成员列表(sshd 按 `/etc/group` 重建补充组) |
| CUDA compat | 启动时探测 `cuInit`:失败才从 `LD_LIBRARY_PATH` 摘掉 `*/compat`;仍失败则还原 |
| SSH 会话环境 | entrypoint 把 PID 1 的环境写进 `/etc/environment`(PAM)与 `/etc/profile.d/superdl-env.sh`(登录 shell)。**用黑名单不用白名单**:剔除 `JUPYTER_TOKEN` / `AUTHORIZED_KEYS`、含 TOKEN/SECRET/PASSWORD/KEY/CREDENTIAL 的变量与 shell 私有变量;**敏感值绝不落盘** |
| SSH 可用性 | sshd 需要 `SYS_CHROOT` / `SETUID` / `SETGID` 三个 capability,平台在 `tenant_security_context()` 里 drop ALL 后单独 add 回。TopoLVM 把 `/root` 挂成 `2777`,entrypoint 起 sshd 前 `chmod g-w,o-w /root`(StrictModes) |
| 用户安装的包 | 必须落在实例盘:entrypoint 下发 `PYTHONUSERBASE=/root/.local` + `PIP_USER=1`,`JULIA_DEPOT_PATH` 前置 `/root/.julia`(并把镜像自带的 `environments/vX.Y` 复制过去)。`/opt/conda`、`/opt/julia` 在容器可写层,Pod 重建即消失。`profile.d` 里定义 `pip` 包装:`$VIRTUAL_ENV` 非空时关掉 `--user` |
| Lab 设置目录 | `lab-overrides.json` 构建期放到 root 属主的 `/opt/superdl/labsettings`,entrypoint 用 `--LabApp.app_settings_dir` 指过去;不用基座默认的 `<app_dir>/settings` |
| SSH host key | 首次生成后持久化到实例盘(`/root/.ssh/host_keys`),`/etc/ssh` 下为符号链接 |
| SSH 公钥 | 读环境变量 `AUTHORIZED_KEYS`(多行)**无条件覆写** `~/.ssh/authorized_keys`(空值也要清空文件),sshd 监听 `22`,仅密钥登录 |
| 工作目录 | 用户数据放 `/root`(实例盘挂载点);数据盘挂 `/root/data` |
| HOME 与运行目录 | `HOME=/root`,Jupyter 的 data 目录落在 `/root` 下。**runtime 与 config 两个目录放容器可写层**:`JUPYTER_RUNTIME_DIR=/run/jupyter`(含 token 明文)、`JUPYTER_CONFIG_DIR=/run/jupyter-config`(配置文件可改鉴权,不落实例盘) |
| 基础镜像 | 与 SKU 的 `cuda_max` 兼容的 CUDA 运行时;Dockerfile 末尾 `ENV NVIDIA_VISIBLE_DEVICES=void` 覆盖 nvidia/cuda 基座的 `all`,可见卡只来自 HAMi / device-plugin 注入的容器 env |
| 容器日志 | entrypoint 把自身与 Jupyter 的 stdout/stderr 经 `sed` 抹掉 `token=` 值再落容器日志(Jupyter 启动横幅带 `?token=`);日志管道侧 Alloy 再抹一次 |

## 默认镜像矩阵(平台自带目录)

选版规则:

- **框架版本**:只上「最新稳定版」+「最后一个支持 CUDA 11.8 的稳定版」;最新版本自己覆盖 11.8 时只留一个。不收 rc/beta。
- **CUDA 线**:`13.2` / `12.9` / `11.8` 三条,取值以**框架官方轮子实际发布的 CUDA**为准,基座 `nvidia/cuda` 的小版本与之对齐。
- **Python**:取该框架支持的**最高**版本;封顶了才降(TF 2.14 → 3.11),PaddlePaddle 用厂商基座自带的 3.10。
- **框架镜像以同线 Miniconda 镜像为父镜像**,推送时只上传框架 wheel 那一层。

| 镜像 tag | 框架 | Python | CUDA | 基座 |
|---|---|---|---|---|
| `miniconda:26.5.3-cu132-py313` | —(干净 conda) | 3.13 | 13.2 | `nvidia/cuda:13.2.1-cudnn-devel-ubuntu24.04` |
| `pytorch:2.13.0-cu132-py313` | PyTorch 2.13.0 | 3.13 | 13.2 | ↑ 同线 miniconda |
| `miniconda:26.5.3-cu129-py313` | —(干净 conda) | 3.13 | 12.9 | `nvidia/cuda:12.9.2-cudnn-devel-ubuntu24.04` |
| `pytorch:2.13.0-cu129-py313` | PyTorch 2.13.0 | 3.13 | 12.9 | ↑ 同线 miniconda |
| `tensorflow:2.21.0-cu129-py313` | TensorFlow 2.21.0 | 3.13 | 12.9 | ↑ 同线 miniconda |
| `miniconda:26.5.3-cu118-py313` | —(干净 conda) | 3.13 | 11.8 | `nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04` |
| `pytorch:2.7.1-cu118-py313` | PyTorch 2.7.1 | 3.13 | 11.8 | ↑ 同线 miniconda |
| `tensorflow:2.14.1-cu118-py311` | TensorFlow 2.14.1 | 3.11 | 11.8 | CUDA 11.8 基座 + py311 conda(TF 2.14 无 cp313 轮子) |
| `datascience:2026.08-py313` | 数据科学栈(无框架、无 CUDA):R 4.5.3 + Julia 1.12.7 + pandas/scikit-learn/scipy/matplotlib/seaborn/statsmodels | 3.13 | 无 | `quay.io/jupyter/datascience-notebook`(`latest` 的 digest 快照) |
| `paddle:3.3.1-cu130-py310` | PaddlePaddle 3.3.1 | 3.10 | 13.0 | `paddlepaddle/paddle:3.3.1-gpu-cuda13.0-cudnn9.13` |
| `paddle:3.3.1-cu129-py310` | PaddlePaddle 3.3.1 | 3.10 | 12.9 | `paddlepaddle/paddle:3.3.1-gpu-cuda12.9-cudnn9.9` |
| `paddle:3.3.1-cu118-py310` | PaddlePaddle 3.3.1 | 3.10 | 11.8 | `paddlepaddle/paddle:3.3.1-gpu-cuda11.8-cudnn8.9` |

已知空档:

- **TensorFlow 无 13.x**:官方尚无 CUDA 13 轮子。
- **PyTorch 2.13.0 两个镜像无 torchaudio**(torchaudio 停在 2.11.0);2.7.1 镜像三件套齐全。
- **PaddlePaddle 的 CUDA 线按厂商**(13.0 / 12.9 / 11.8),用飞桨官方基座补平台契约层,不自建。

## 构建与推送

一份 `Dockerfile` + `entrypoint.sh` + `superdl_jupyter_auth.py` + `lab-overrides.json`,三种用法靠 build-arg 区分(文件头注释有全表)。构建上下文是本目录。基座一律钉 digest。
所有安装步骤之后统一跑一次 `apt full-upgrade`;OpenSSH 版本由基座 OS 决定(24.04 → 9.6p1、22.04 → 8.9p1),不从源码自建;该层在 CUDA 基座上约 **4GB**,不 hold CUDA 包。

```bash
cd deploy/instance-images
REG=<registry>/superdl
JUP="jupyterlab==4.6.3 jupyter-ai[jupyternaut,magics]==3.1.3 jupyter-resource-usage==1.3.0 jupyterlab-language-pack-zh-CN==4.5.post3 ipykernel==7.3.0"
CONDA=/opt/conda/bin:   # 只有自建 conda 的那几步传;厂商基座不传(它们没有 /opt/conda)
# 安装包 SHA-256 钉版(缺了构建即拒;值 = 官方发布哈希,换安装包版本时同步换):
CONDA_SHA313=66f7c434bbdc7a4c5687b7e56cde724f73954d1322ffb273c6e387f12fbcdc03  # Miniconda3-py313_26.5.3-2-Linux-x86_64.sh
CONDA_SHA311=cdca3dd8440759bb87c60b227e26946263fe2856b30b2bcfdd964c38254fb8eb  # Miniconda3-py311_26.5.3-2-Linux-x86_64.sh

# ---- CUDA 13.2 线 ----
docker build -t $REG/miniconda:26.5.3-cu132-py313 \
  --build-arg BASE_IMAGE=nvidia/cuda:13.2.1-cudnn-devel-ubuntu24.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py313_26.5.3-2-Linux-x86_64.sh \
  --build-arg MINICONDA_SHA256=$CONDA_SHA313 \
  --build-arg CONDA_PATH_PREFIX=$CONDA --build-arg JUPYTER_PACKAGES="$JUP" .
docker build -t $REG/pytorch:2.13.0-cu132-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu132-py313 \
  --build-arg FRAMEWORK_PIP="torch==2.13.0+cu132 torchvision==0.28.0+cu132" \
  --build-arg FRAMEWORK_PIP_INDEX=https://download.pytorch.org/whl/cu132 .

# ---- CUDA 12.9 线 ----
docker build -t $REG/miniconda:26.5.3-cu129-py313 \
  --build-arg BASE_IMAGE=nvidia/cuda:12.9.2-cudnn-devel-ubuntu24.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py313_26.5.3-2-Linux-x86_64.sh \
  --build-arg CONDA_PATH_PREFIX=$CONDA --build-arg JUPYTER_PACKAGES="$JUP" .
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
  --build-arg CONDA_PATH_PREFIX=$CONDA --build-arg JUPYTER_PACKAGES="$JUP" .
docker build -t $REG/pytorch:2.7.1-cu118-py313 \
  --build-arg BASE_IMAGE=$REG/miniconda:26.5.3-cu118-py313 \
  --build-arg FRAMEWORK_PIP="torch==2.7.1+cu118 torchvision==0.22.1+cu118 torchaudio==2.7.1+cu118" \
  --build-arg FRAMEWORK_PIP_INDEX=https://download.pytorch.org/whl/cu118 .
docker build -t $REG/tensorflow:2.14.1-cu118-py311 \
  --build-arg BASE_IMAGE=nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 \
  --build-arg MINICONDA_INSTALLER=Miniconda3-py311_26.5.3-2-Linux-x86_64.sh \
  --build-arg CONDA_PATH_PREFIX=$CONDA --build-arg JUPYTER_PACKAGES="$JUP" \
  --build-arg FRAMEWORK_PIP="tensorflow==2.14.1" .
# TF 2.14 不用 [and-cuda](tensorrt==8.5.3.1 已从 PyPI 下架),直接用基座系统 CUDA。

# ---- DataScience(jupyter docker-stacks 基座,只补平台契约层;CPU 向,无 CUDA)----
# 上游只有滚动的 latest,构建时钉当次 digest;版本号用镜像快照月份
docker build -t $REG/datascience:2026.08-py313 \
  --build-arg BASE_IMAGE=quay.io/jupyter/datascience-notebook@sha256:<当次 digest> \
  --build-arg JUPYTER_PACKAGES="$JUP" .
# 不要 chown -R /opt/conda;Lab 设置放 root 属主的 /opt/superdl/labsettings,用户装包走 PYTHONUSERBASE/JULIA_DEPOT_PATH 落实例盘。

# ---- PaddlePaddle(厂商基座,只补平台契约层;基座自带 python3.10 与 paddle)----
PADDLE=ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddle
for pair in "cu130:3.3.1-gpu-cuda13.0-cudnn9.13" "cu129:3.3.1-gpu-cuda12.9-cudnn9.9" "cu118:3.3.1-gpu-cuda11.8-cudnn8.9"; do
  docker build -t "$REG/paddle:3.3.1-${pair%%:*}-py310" \
    --build-arg BASE_IMAGE="$PADDLE:${pair#*:}" --build-arg JUPYTER_PACKAGES="$JUP" .
done
```

推送前自检(五步;`deploy/instance-images` 下任何文件改动后重建都要重跑):

```bash
IMG=$REG/pytorch:2.13.0-cu132-py313
# ① 扩展能在目标基座的 jupyter_server 上 import
docker run --rm --entrypoint python $IMG -c "import sys; sys.path.insert(0,'/opt/superdl'); import superdl_jupyter_auth; print('ok')"
# ② Jupyter 套件齐全,jupyter-ai 的 jupyternaut persona 在位
docker run --rm --entrypoint bash $IMG -lc 'pip list | grep -iE "jupyterlab |jupyter_ai|jupyter-resource-usage|language-pack|ipykernel";
  python -c "from importlib.metadata import entry_points,version; assert \"jupyternaut\" in [e.name for e in entry_points(group=\"jupyter_ai.personas\")]; version(\"jupyter-ai-litellm\"); print(\"ai ok\")"'
# ③ 真起一次(生产 capabilities + 2777 的 /root),验票据、界面语言默认值、SSH
FAKE=$(mktemp -d); chmod 2777 $FAKE; ssh-keygen -q -t ed25519 -f /tmp/tkey -N ""
docker run -d --name jcheck --cap-drop=ALL --cap-add=SYS_CHROOT --cap-add=SETUID --cap-add=SETGID \
  -e JUPYTER_TOKEN=selfcheck -e AUTHORIZED_KEYS="$(cat /tmp/tkey.pub)" \
  -v $FAKE:/root -p 127.0.0.1:18888:8888 $IMG && sleep 28
curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:18888/superdl-bootstrap?code=x&exp=1&sig=y'   # 期望 403
TIK=$(python3 -c 'import hmac,hashlib,time;e=str(int(time.time())+60);print(f"code=c0&exp={e}&sig="+hmac.new(b"selfcheck",f"c0.{e}".encode(),hashlib.sha256).hexdigest())')
curl -s -o /dev/null -D - -c /tmp/jar "http://127.0.0.1:18888/superdl-bootstrap?$TIK" | grep -i '^location:'  # 期望 /lab
# 界面语言验「默认值」,不验「语言包装了没」
curl -s -b /tmp/jar 'http://127.0.0.1:18888/lab/api/settings/@jupyterlab/translation-extension:plugin' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["schema"]["properties"]["locale"]["default"])'   # 期望 zh_CN
# ④ 真连一次 SSH(验认证,不只验 sshd 起没起)
IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' jcheck)
ssh -i /tmp/tkey -o StrictHostKeyChecking=no -o BatchMode=yes root@$IP 'echo SSHOK; python -V; id -Gn'
docker exec jcheck sh -c 'cat /etc/environment /etc/profile.d/superdl-env.sh | grep -ciE "selfcheck|ssh-ed25519"'  # 期望 0
docker rm -f jcheck; rm -rf $FAKE
# ⑤ 推送(push 权限机器人;tag 可覆盖重推,不搞 -rN 后缀)
docker login <registry> -u 'robot$superdl+push'
docker push $IMG
# 取本次 digest(管理端登记填它,不是 tag)
echo "$IMG@$(docker inspect --format '{{index .RepoDigests 0}}' $IMG | cut -d@ -f2)"
```

GPU 可用性在有卡的节点上验:推送并在管理端登记后,建一台实例跑 `python -c "import torch;print(torch.cuda.is_available())"` / `tf.config.list_physical_devices('GPU')` / `paddle.utils.run_check()`。

推完之后的上线动作(管理端登记 digest、`image_ref` 钉 digest、重推 tag 后换 ref、实例 ref 是创建时快照)见 `deploy/cluster/runbooks/image-prewarm.md`。
