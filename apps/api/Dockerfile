FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

WORKDIR /srv/api
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
ENV PATH="/srv/api/.venv/bin:$PATH"

EXPOSE 8000
# 同一镜像双入口:serve(默认)/ worker(compose 里覆盖 command)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
