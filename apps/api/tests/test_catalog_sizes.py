"""Platform image download size: per-architecture bytes from an index or a single manifest, the
registry credentials only for the configured host, and the patrol that fills and retries."""

import hashlib
import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.platform_config import RuntimeConfig
from app.core.registry_push import RegistryClient, RegistryPushError, RegistryTarget
from app.modules.catalog import sizes
from app.modules.catalog.models import PlatformImage
from tests.fake_registry import FakeRegistry

REPO = "superdl/pytorch"


def _store(reg: FakeRegistry, doc: dict[str, Any], *refs: str) -> str:
    body = json.dumps(doc).encode()
    digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
    for ref in (digest, *refs):
        reg.manifests[(REPO, ref)] = body
    return digest


def _image(layer_sizes: list[int], config_digest: str = "sha256:" + "0" * 64) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": config_digest, "size": 2},
        "layers": [{"digest": f"sha256:{i:064x}", "size": s} for i, s in enumerate(layer_sizes)],
    }


def _client(reg: FakeRegistry, username: str = "robot$pull") -> RegistryClient:
    target = RegistryTarget(
        "reg.test", REPO, username, "secret" if username else "", actions="pull"
    )
    return RegistryClient(target, transport=reg.transport())


def test_index_sizes_per_architecture_skip_attestations():
    reg = FakeRegistry()
    amd = _store(reg, _image([100, 200]))
    arm = _store(reg, _image([150]))
    attestation = _store(reg, _image([1]))
    platforms = [
        (amd, {"os": "linux", "architecture": "amd64"}),
        (arm, {"os": "linux", "architecture": "arm64"}),
        (attestation, {"os": "unknown", "architecture": "unknown"}),
    ]
    _store(reg, {"manifests": [{"digest": d, "platform": p} for d, p in platforms]}, "2.13")
    with _client(reg) as client:
        assert client.download_sizes("2.13") == {"amd64": 300, "arm64": 150}
    assert reg.token_requests == [(f"repository:{REPO}:pull", True)]


def test_single_manifest_takes_the_architecture_from_its_config():
    reg = FakeRegistry()
    config = json.dumps({"architecture": "arm64", "os": "linux"}).encode()
    config_digest = f"sha256:{hashlib.sha256(config).hexdigest()}"
    reg.blobs[config_digest] = config
    _store(reg, _image([40, 2], config_digest), "1.0")
    with _client(reg, username="") as client:
        assert client.download_sizes("1.0") == {"arm64": 42}
    assert reg.token_requests == [(f"repository:{REPO}:pull", False)]


def test_missing_manifest_raises():
    with _client(FakeRegistry()) as client, pytest.raises(RegistryPushError):
        client.download_sizes("nope")


def _cfg(host: str) -> RuntimeConfig:
    return cast(
        RuntimeConfig,
        SimpleNamespace(
            registry_host=host,
            registry_robot_name="robot$pull",
            registry_robot_secret="secret",
            registry_ca_pem="",
        ),
    )


@pytest.mark.parametrize(
    ("ref", "host", "user"),
    [
        ("reg.test/superdl/pytorch:2.13@sha256:" + "a" * 64, "reg.test", "robot$pull"),
        ("ghcr.io/org/tool:1.0", "ghcr.io", ""),
    ],
)
def test_robot_credentials_only_for_the_configured_registry(
    monkeypatch: pytest.MonkeyPatch, ref: str, host: str, user: str
):
    seen: list[RegistryTarget] = []

    class Capture:
        def __init__(self, target: RegistryTarget) -> None:
            seen.append(target)

        def __enter__(self) -> "Capture":
            return self

        def __exit__(self, *exc: object) -> None:
            del exc

        def download_sizes(self, reference: str) -> dict[str, int]:
            del reference
            return {"amd64": 1}

    monkeypatch.setattr(sizes, "RegistryClient", Capture)
    assert sizes.measure_download_bytes(ref, _cfg("reg.test")) == {"amd64": 1}
    assert (seen[0].host, seen[0].username, seen[0].actions) == (host, user, "pull")


async def _seed(sm: async_sessionmaker[AsyncSession], ref: str) -> int:
    async with sm() as session:
        img = PlatformImage(
            framework="PyTorch",
            framework_version="2.13",
            python_version="3.13",
            cuda_version="12.9",
            image_ref=ref,
        )
        session.add(img)
        await session.commit()
        return img.id


async def _sizes(sm: async_sessionmaker[AsyncSession], image_id: int) -> tuple[Any, Any]:
    async with sm() as session:
        row = (
            await session.execute(
                select(PlatformImage.download_bytes, PlatformImage.download_ref).where(
                    PlatformImage.id == image_id
                )
            )
        ).one()
        return row[0], row[1]


async def test_patrol_measures_once_retries_failures_and_remeasures_a_new_ref(
    sm: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
):
    ref = "reg.test/superdl/pytorch:2.13"
    image_id = await _seed(sm, ref)
    calls: list[str] = []

    def failing(image_ref: str, cfg: RuntimeConfig) -> dict[str, int]:
        del cfg
        calls.append(image_ref)
        raise RegistryPushError("manifest read refused", status=503)

    monkeypatch.setattr(sizes, "measure_download_bytes", failing)
    assert await sizes.image_size_patrol(sm) == {"measured": 0, "failed": 1}
    assert await _sizes(sm, image_id) == (None, None)

    def measured(image_ref: str, cfg: RuntimeConfig) -> dict[str, int]:
        del cfg
        calls.append(image_ref)
        return {"amd64": 7_000_000_000}

    monkeypatch.setattr(sizes, "measure_download_bytes", measured)
    assert await sizes.image_size_patrol(sm) == {"measured": 1, "failed": 0}
    assert await _sizes(sm, image_id) == ({"amd64": 7_000_000_000}, ref)
    assert await sizes.image_size_patrol(sm) == {"measured": 0, "failed": 0}

    async with sm() as session:
        img = await session.get(PlatformImage, image_id)
        assert img is not None
        img.image_ref = "reg.test/superdl/pytorch:2.14"
        await session.commit()
    assert await sizes.image_size_patrol(sm) == {"measured": 1, "failed": 0}
    assert calls == [ref, ref, "reg.test/superdl/pytorch:2.14"]
