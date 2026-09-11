# tag: python3.13-trixie-slim(digest 钉死;升版本同步改 tag 注释与 digest)
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim@sha256:d1e005e6f5aac724b7554db95f1c128a77d8d35b59ebe70e188852b4bdad3a3d

WORKDIR /srv/api
# 运行期不产生 .pyc(构建期 UV_COMPILE_BYTECODE 预编译)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
ENV PATH="/srv/api/.venv/bin:$PATH"

# 非 root 运行
RUN useradd --system --uid 10001 --no-create-home superdl
USER 10001

EXPOSE 8000
# 同一镜像双入口:serve(默认)/ worker(compose 覆盖 command)
# --proxy-headers 配合 FORWARDED_ALLOW_IPS(deploy/app/k8s/00-namespace-config.yaml);--limit-concurrency 单进程在途连接上限
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--limit-concurrency", "1024"]
