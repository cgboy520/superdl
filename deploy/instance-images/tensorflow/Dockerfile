# 平台镜像 · TensorFlow + JupyterLab(契约见 ../README.md)
# 基于 jupyter/tensorflow-notebook CUDA 版(自带 conda + TensorFlow + JupyterLab),补齐平台契约:
# Jupyter 绑 0.0.0.0、读 JUPYTER_TOKEN、SSH 公钥注入、工作目录 /root。
# 基座钉 digest:浮动 tag 会在重建时静默换基座
FROM quay.io/jupyter/tensorflow-notebook:cuda-latest@sha256:fc4c5b03dfbaa5d358375810a7b721bfcbe65ba1983a6829bc09297d334f0d63

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
COPY superdl_jupyter_auth.py /opt/superdl/superdl_jupyter_auth.py
RUN chmod +x /usr/local/bin/superdl-entrypoint.sh

WORKDIR /root
EXPOSE 22 8888
ENTRYPOINT ["/usr/local/bin/superdl-entrypoint.sh"]
