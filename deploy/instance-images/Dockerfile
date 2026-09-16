# check=skip=InvalidDefaultArgInFrom
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ARG DEBIAN_FRONTEND=noninteractive

ARG MINICONDA_INSTALLER=""
ARG MINICONDA_SHA256=""
ARG CONDA_PATH_PREFIX=""
ENV PATH=${CONDA_PATH_PREFIX}$PATH
RUN if [[ -n "$MINICONDA_INSTALLER" ]]; then \
      if [[ -z "$MINICONDA_SHA256" ]]; then \
        echo "MINICONDA_SHA256 is required (supply-chain pin of MINICONDA_INSTALLER)" >&2; exit 1; \
      fi \
      && apt-get update \
      && apt-get install -y --no-install-recommends curl ca-certificates bzip2 \
      && rm -rf /var/lib/apt/lists/* \
      && curl -fsSL -o /tmp/miniconda.sh "https://repo.anaconda.com/miniconda/${MINICONDA_INSTALLER}" \
      && echo "${MINICONDA_SHA256}  /tmp/miniconda.sh" | sha256sum -c - \
      && bash /tmp/miniconda.sh -b -p /opt/conda \
      && rm -f /tmp/miniconda.sh \
      && /opt/conda/bin/conda clean -afy; \
    fi

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

RUN if [[ -n "$JUPYTER_PACKAGES" ]]; then \
      pip install --no-cache-dir --root-user-action=ignore ${JUPYTER_PACKAGES} \
      && jupyter lab --version; \
    fi

ARG FRAMEWORK_PIP=""
ARG FRAMEWORK_PIP_INDEX=""
RUN if [[ -n "$FRAMEWORK_PIP" ]]; then \
      pip install --no-cache-dir --root-user-action=ignore \
        ${FRAMEWORK_PIP_INDEX:+--index-url ${FRAMEWORK_PIP_INDEX}} ${FRAMEWORK_PIP}; \
    fi

RUN apt-get update \
    && apt-get -y -o APT::Install-Recommends=false full-upgrade \
    && apt-get -y autoremove \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/root
ENV NVIDIA_VISIBLE_DEVICES=void

COPY entrypoint.sh /usr/local/bin/superdl-entrypoint.sh
COPY superdl_jupyter_auth.py /opt/superdl/superdl_jupyter_auth.py
COPY lab-overrides.json /opt/superdl/labsettings/overrides.json
RUN chmod +x /usr/local/bin/superdl-entrypoint.sh \
    && chmod 0755 /opt/superdl/labsettings \
    && chmod 0644 /opt/superdl/labsettings/overrides.json

WORKDIR /root
EXPOSE 22 8888
ENTRYPOINT ["/usr/local/bin/superdl-entrypoint.sh"]
