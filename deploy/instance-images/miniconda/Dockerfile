# 平台镜像 · Miniconda + JupyterLab(契约见 ../README.md)
# 基于 continuumio/miniconda3(干净 conda 基座,不预装 DL 框架,用户自建环境),
# 补装 JupyterLab 并补齐平台契约:Jupyter 绑 0.0.0.0、读 JUPYTER_TOKEN、SSH 公钥注入、工作目录 /root。
# 基座钉 digest:浮动 tag 会在重建时静默换基座
FROM continuumio/miniconda3:26.5.3-1@sha256:1808b31ef43e9c521cde5884ba4df9ec26d8d503a314cea787590f8550358a63

USER root

# JupyterLab 是唯一预装件:平台契约的入口(8888/token 鉴权 + superdl_jupyter_auth 扩展)靠它提供
RUN pip install --no-cache-dir "jupyterlab==4.6.3"

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
