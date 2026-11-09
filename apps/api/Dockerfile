FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim@sha256:6ff812e34a0e14b4b8bc9ddf25e77b3e16bac6e0c4d9b67641f40366fa382d4c

WORKDIR /srv/api
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1

# Ceph CLI (subvolumes and keys through the restricted admin key) and libcephfs for the system
# python3 (tenant storage path checks, app/core/ceph/pathcheck.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ceph-common python3-cephfs \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
ENV PATH="/srv/api/.venv/bin:$PATH"

RUN useradd --system --uid 10001 --no-create-home superdl
USER 10001

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--limit-concurrency", "1024"]
