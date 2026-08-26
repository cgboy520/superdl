# check=skip=InvalidDefaultArgInFrom
# 平台实例镜像(契约见 README.md)。一份 Dockerfile 三种用法,靠 build-arg 区分:
#   ① CUDA 基座 → Miniconda 镜像:BASE_IMAGE=nvidia/cuda:<x.y.z>-cudnn-devel-*,MINICONDA_INSTALLER=<installer>,
#      JUPYTER_PACKAGES=<jupyter 套件>;产出的就是平台的 Miniconda 镜像,也是同 CUDA 线框架镜像的父镜像。
#   ② Miniconda 镜像 → 框架镜像:BASE_IMAGE=<上一步产物>,只给 FRAMEWORK_PIP(+ FRAMEWORK_PIP_INDEX);
#      conda / ssh / jupyter 层直接复用父镜像的 layer,推送时只上传框架 wheel 那一层。
#   ③ 第三方框架基座(如 PaddlePaddle 官方镜像)→ 平台镜像:BASE_IMAGE=<厂商镜像>,只给 JUPYTER_PACKAGES,
#      用基座自带的 python/pip(不装 conda)。
# BASE_IMAGE 故意不给默认值:没有「默认那一个」,漏传即构建失败(上面的 check 指令是有意跳过校验器告警)。
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

# ---------- ① Miniconda(仅 CUDA 基座那一步传;第三方基座与框架镜像留空跳过)----------
# 装到 /opt/conda 并进 PATH:平台契约要求 jupyter/python 在 PATH 上,且 HOME=/root 可写。
ARG MINICONDA_INSTALLER=""
ENV PATH=/opt/conda/bin:$PATH
RUN if [[ -n "$MINICONDA_INSTALLER" ]]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends curl ca-certificates bzip2 \
      && rm -rf /var/lib/apt/lists/* \
      && curl -fsSL -o /tmp/miniconda.sh "https://repo.anaconda.com/miniconda/${MINICONDA_INSTALLER}" \
      && bash /tmp/miniconda.sh -b -p /opt/conda \
      && rm -f /tmp/miniconda.sh \
      && /opt/conda/bin/conda clean -afy; \
    fi

# ---------- ② sshd(平台契约:仅密钥登录的 22 端口)----------
# 已有 sshd 的基座跳过,避免框架镜像重复跑 apt 生成无用 layer。
RUN if ! command -v sshd >/dev/null 2>&1; then \
      apt-get update \
      && apt-get install -y --no-install-recommends openssh-server \
      && rm -rf /var/lib/apt/lists/*; \
    fi \
    && mkdir -p /var/run/sshd /root/.ssh \
    && chmod 700 /root/.ssh \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config

# ---------- ③ Jupyter 套件(版本钉死在 README 的构建命令里)----------
# ipykernel 必装:jupyterlab 不依赖它,少了它实例里没有 Python 内核。
ARG JUPYTER_PACKAGES=""
RUN if [[ -n "$JUPYTER_PACKAGES" ]]; then \
      pip install --no-cache-dir --root-user-action=ignore ${JUPYTER_PACKAGES} \
      && jupyter lab --version; \
    fi

# ---------- ④ 框架 wheel(框架镜像那一步传)----------
# FRAMEWORK_PIP_INDEX 用于 PyTorch 的 CUDA 专用索引(download.pytorch.org/whl/cuXXX)。
ARG FRAMEWORK_PIP=""
ARG FRAMEWORK_PIP_INDEX=""
RUN if [[ -n "$FRAMEWORK_PIP" ]]; then \
      pip install --no-cache-dir --root-user-action=ignore \
        ${FRAMEWORK_PIP_INDEX:+--index-url ${FRAMEWORK_PIP_INDEX}} ${FRAMEWORK_PIP}; \
    fi

# 平台文件放最后:改 entrypoint / 鉴权扩展 / Lab 默认设置只失效这几层,不触发上面的重装。
COPY entrypoint.sh /usr/local/bin/superdl-entrypoint.sh
COPY superdl_jupyter_auth.py /opt/superdl/superdl_jupyter_auth.py
COPY lab-overrides.json /opt/superdl/lab-overrides.json
RUN chmod +x /usr/local/bin/superdl-entrypoint.sh

WORKDIR /root
EXPOSE 22 8888
ENTRYPOINT ["/usr/local/bin/superdl-entrypoint.sh"]
