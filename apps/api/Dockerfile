# tag: python3.13-trixie-slim(digest 钉死保证可复现;升版本时同步更新 tag 注释与 digest)
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim@sha256:d1e005e6f5aac724b7554db95f1c128a77d8d35b59ebe70e188852b4bdad3a3d

WORKDIR /srv/api
# 只读根文件系统下运行期不产生 .pyc(构建期已 UV_COMPILE_BYTECODE 预编译)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
ENV PATH="/srv/api/.venv/bin:$PATH"

# 非 root 运行(K8s 侧再配 runAsNonRoot/readOnlyRootFilesystem)
RUN useradd --system --uid 10001 --no-create-home superdl
USER 10001

EXPOSE 8000
# 同一镜像双入口:serve(默认)/ worker(compose 里覆盖 command)
# --proxy-headers 配合 FORWARDED_ALLOW_IPS(deploy/app/k8s/00-namespace-config.yaml);
# 不配则只信任 127.0.0.1,client.host 恒为代理 IP。
# --limit-concurrency:单进程在途连接硬顶。请求体已有 1MiB 上限(app/core/body_limit.py),
# 连接数兜底内存:1024 在途 × (≤1MiB body + 连接开销) 仍远低于 Pod 1GiB;
# 超限 uvicorn 直接 503 不进路由。正常流量远不及此(DB 池 10 才是真实瓶颈)。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--limit-concurrency", "1024"]
