# bookworm: the Ceph release repository builds Tentacle for bookworm, not trixie.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca

WORKDIR /srv/api
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1

# Ceph CLI (subvolumes and keys through the restricted admin key) and libcephfs for the system
# python3 (tenant storage path checks, app/core/ceph/pathcheck.py), Tentacle from the Ceph release
# repository (signing key 08B73419AC32B4E966C1A330E84AC2C0460F3994): clients before Tentacle
# cannot read AES-256 cephx keys.
COPY ceph-release.asc /usr/share/keyrings/ceph-release.asc
RUN echo "deb [signed-by=/usr/share/keyrings/ceph-release.asc] https://download.ceph.com/debian-tentacle bookworm main" \
        > /etc/apt/sources.list.d/ceph.list \
    && apt-get update \
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
