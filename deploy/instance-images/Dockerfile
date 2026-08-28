# check=skip=InvalidDefaultArgInFrom
# 平台实例镜像(契约见 README.md)。一份 Dockerfile 三种用法,靠 build-arg 区分:
#   ① CUDA 基座 → Miniconda 镜像:BASE_IMAGE=nvidia/cuda:<x.y.z>-cudnn-devel-*,MINICONDA_INSTALLER=<installer>,
#      CONDA_PATH_PREFIX=/opt/conda/bin:,JUPYTER_PACKAGES=<jupyter 套件>;产出的也是同 CUDA 线框架镜像的父镜像。
#   ② Miniconda 镜像 → 框架镜像:BASE_IMAGE=<上一步产物>,只给 FRAMEWORK_PIP(+ FRAMEWORK_PIP_INDEX);
#      conda / ssh / jupyter 层直接复用父镜像的 layer,推送时只上传框架 wheel 那一层。
#   ③ 第三方框架基座(如 PaddlePaddle 官方镜像)→ 平台镜像:BASE_IMAGE=<厂商镜像>,只给 JUPYTER_PACKAGES,
#      用基座自带的 python/pip(不装 conda,也不动 PATH)。
# BASE_IMAGE 不给默认值,漏传即构建失败(顶部 check 指令为此跳过校验器告警)。
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
# ARG 而非 ENV:noninteractive 只该在构建期生效,泄漏到运行时会让用户在实例里 apt install 拿不到交互提示
ARG DEBIAN_FRONTEND=noninteractive

# ---------- ① Miniconda(仅 CUDA 基座那一步传;第三方基座与框架镜像留空跳过)----------
# CONDA_PATH_PREFIX 与 MINICONDA_INSTALLER 同批传:PATH 无条件前置会让没有 conda 的厂商基座
# 带上一条不存在的目录,也会让框架镜像把 /opt/conda/bin 重复一遍。
ARG MINICONDA_INSTALLER=""
ARG CONDA_PATH_PREFIX=""
ENV PATH=${CONDA_PATH_PREFIX}$PATH
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
# 只在「从外部基座起步」的两种用法里跑(①③):框架镜像的父镜像已装好,再跑一遍只会白生一层。
# 基座自带 sshd 时也要走一次 --only-upgrade,否则厂商基座里的 openssh 停在打包那天的补丁级。
ARG JUPYTER_PACKAGES=""
RUN if [[ -n "${MINICONDA_INSTALLER}${JUPYTER_PACKAGES}" ]]; then \
      apt-get update; \
      if command -v sshd >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends --only-upgrade openssh-server; \
      else \
        apt-get install -y --no-install-recommends openssh-server; \
      fi; \
      rm -rf /var/lib/apt/lists/*; \
      mkdir -p /var/run/sshd /root/.ssh /etc/ssh/sshd_config.d; \
      chmod 700 /root/.ssh; \
      printf 'PasswordAuthentication no\nPermitRootLogin prohibit-password\n' \
        > /etc/ssh/sshd_config.d/00-superdl.conf; \
      grep -q '^Include /etc/ssh/sshd_config.d/\*.conf' /etc/ssh/sshd_config \
        || sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' /etc/ssh/sshd_config; \
    fi

# ---------- ③ Jupyter 套件(版本钉死在 README 的构建命令里)----------
# ipykernel 必装:jupyterlab 不依赖它,少了它实例里没有 Python 内核。
# jupyter-ai 必须带 [jupyternaut,magics] extra:裸装只有聊天壳子,没有任何模型提供方。
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

# ---------- ⑤ 打齐系统补丁 ----------
# 装完所有东西后再升,发行版的安全回补(含 openssh)一次到位。
RUN apt-get update \
    && apt-get -y -o APT::Install-Recommends=false full-upgrade \
    && apt-get -y autoremove \
    && rm -rf /var/lib/apt/lists/*

# 平台契约:HOME 一律 /root(实例盘挂载点)。jupyter docker-stacks 系基座默认 HOME=/home/jovyan,
# 而共享池 userns 下那个目录不可写,ssh / kubectl exec 进去会刷 .bash_profile Permission denied。
ENV HOME=/root

# 平台文件放最后:改 entrypoint / 鉴权扩展 / Lab 默认设置只失效这几层,不触发上面的重装。
COPY entrypoint.sh /usr/local/bin/superdl-entrypoint.sh
COPY superdl_jupyter_auth.py /opt/superdl/superdl_jupyter_auth.py
COPY lab-overrides.json /opt/superdl/labsettings/overrides.json
RUN chmod +x /usr/local/bin/superdl-entrypoint.sh \
    && chmod 0755 /opt/superdl/labsettings \
    && chmod 0644 /opt/superdl/labsettings/overrides.json

WORKDIR /root
EXPOSE 22 8888
ENTRYPOINT ["/usr/local/bin/superdl-entrypoint.sh"]
