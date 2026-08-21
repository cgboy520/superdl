FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

WORKDIR /srv/api
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
ENV PATH="/srv/api/.venv/bin:$PATH"

# 非 root 运行(K8s 侧再配 runAsNonRoot/readOnlyRootFilesystem)
RUN useradd --system --uid 10001 --no-create-home superdl
USER 10001

EXPOSE 8000
# 同一镜像双入口:serve(默认)/ worker(compose 里覆盖 command)
# --proxy-headers 配合 FORWARDED_ALLOW_IPS(见 deploy/app/k8s/00-namespace-config.yaml):
# 不信任前置代理的地址,client.host 恒为代理 IP —— 按 IP 限流会坍缩成一个桶、
# audit_log 的源 IP 全部失真(等保溯源要求)。该环境变量不配则只信任 127.0.0.1。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
