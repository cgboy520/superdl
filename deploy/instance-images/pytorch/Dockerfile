# 平台镜像 · PyTorch + JupyterLab(契约见 ../README.md)
# 基于 jupyter/pytorch-notebook(自带 conda + PyTorch + JupyterLab),补齐平台契约:
# Jupyter 绑 0.0.0.0、读 JUPYTER_TOKEN、SSH 公钥注入、工作目录 /root。
# 基座钉 digest:cuda12-latest 是浮动 tag,重建会静默换基座(违背自家「禁止 latest」)
FROM quay.io/jupyter/pytorch-notebook:cuda12-latest@sha256:85ab930435b7afc06396e2949a4fe508d027a7980a319bec6a92f827578e5343

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/sshd /root/.ssh \
    && chmod 700 /root/.ssh \
    # 仅密钥登录(硬规范:禁密码)
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config

COPY entrypoint.sh /usr/local/bin/superdl-entrypoint.sh
RUN chmod +x /usr/local/bin/superdl-entrypoint.sh

WORKDIR /root
EXPOSE 22 8888
ENTRYPOINT ["/usr/local/bin/superdl-entrypoint.sh"]
