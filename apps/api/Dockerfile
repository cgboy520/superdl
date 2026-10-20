FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim@sha256:99a2ec276b9a91b53fc2a9b3f5a6deef2e0e854d1af80cf8f2ccc61cef4e2028

WORKDIR /srv/api
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
ENV PATH="/srv/api/.venv/bin:$PATH"

RUN useradd --system --uid 10001 --no-create-home superdl
USER 10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--limit-concurrency", "1024"]
