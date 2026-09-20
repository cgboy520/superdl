FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim@sha256:5b7499c3e4048c8f9afcca908c2f2c486923cd441b857976fb6a871e6a090737

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
