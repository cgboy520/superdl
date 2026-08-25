# check=skip=InvalidDefaultArgInFrom
# 平台镜像(契约见 README.md):pytorch / tensorflow / miniconda 三个镜像共用本文件,
# 差异只有基座 BASE_IMAGE(digest 钉在 README 的构建命令里,浮动 tag 会在重建时静默换基座)
# 与 miniconda 基座需补装的 JupyterLab。
# 在基座之上补齐平台契约:Jupyter 绑 0.0.0.0、读 JUPYTER_TOKEN、SSH 公钥注入、工作目录 /root。
# BASE_IMAGE 故意不给默认值:三个基座没有「默认那一个」,漏传即构建失败(上面的 check 指令只是告诉
# BuildKit 校验器这是有意为之)。
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root

# 基座不带 JupyterLab 时(continuumio/miniconda3)由 JUPYTERLAB_VERSION 指定版本补装:
# 它是平台契约的入口(8888/token 鉴权 + superdl_jupyter_auth 扩展);
# jupyter/*-notebook 基座自带 JupyterLab,留空即跳过。
ARG JUPYTERLAB_VERSION=""
RUN if [ -n "$JUPYTERLAB_VERSION" ]; then pip install --no-cache-dir "jupyterlab==${JUPYTERLAB_VERSION}"; fi

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/sshd /root/.ssh \
    && chmod 700 /root/.ssh \
    # 仅密钥登录(硬规范:禁密码)
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config

COPY entrypoint.sh /usr/local/bin/superdl-entrypoint.sh
COPY superdl_jupyter_auth.py /opt/superdl/superdl_jupyter_auth.py
RUN chmod +x /usr/local/bin/superdl-entrypoint.sh

WORKDIR /root
EXPOSE 22 8888
ENTRYPOINT ["/usr/local/bin/superdl-entrypoint.sh"]
