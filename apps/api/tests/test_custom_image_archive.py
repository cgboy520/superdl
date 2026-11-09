"""docker save archive import: both layouts (classic layer.tar and Docker 25+ OCI blobs), the three
upload formats (.tar, .tar.gz, .zip with a tar or an unpacked tree), symlinked layers, streamed
large layers, diff-id verification and every rejection reason."""

import gzip
import hashlib
import io
import json
import os
import tarfile
import zipfile
import zlib
from pathlib import Path
from typing import Any

import pytest

from app.core.registry_push import RegistryClient, RegistryTarget
from app.modules.customimages import archive
from app.modules.customimages.archive import ChunkReader, ImportFailure, import_archive
from tests.fake_registry import FakeRegistry

REPO = "superdl-users/u1/myenv"
DEV = ["/usr/local/bin/superdl-entrypoint.sh"]


def tar_bytes(files: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return buf.getvalue()


def layer_tar(seed: int, size: int = 4096) -> bytes:
    return tar_bytes({f"opt/file-{seed}": os.urandom(size)})


def _sha(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _config(layers: list[bytes], **over: Any) -> bytes:
    body: dict[str, Any] = {
        "architecture": "amd64",
        "os": "linux",
        "config": {"Entrypoint": DEV, "ExposedPorts": {"22/tcp": {}, "8888/tcp": {}}},
        "rootfs": {"type": "layers", "diff_ids": [_sha(layer) for layer in layers]},
    }
    body.update(over)
    return json.dumps(body).encode()


def classic(layers: list[bytes], config: bytes | None = None, *, share_last: bool = False) -> bytes:
    """docker save before 25: <id>/layer.tar per layer, <config>.json, manifest.json."""
    config = config or _config(layers)
    config_name = f"{hashlib.sha256(config).hexdigest()}.json"
    files: dict[str, bytes] = {config_name: config}
    links: dict[str, str] = {}
    names: list[str] = []
    for i, layer in enumerate(layers):
        name = f"l{i}/layer.tar"
        if share_last and i == len(layers) - 1:
            links[name] = "../l0/layer.tar"
        else:
            files[name] = layer
        names.append(name)
    files["manifest.json"] = json.dumps(
        [{"Config": config_name, "RepoTags": ["myenv:v1"], "Layers": names}]
    ).encode()
    return tar_bytes(files, links)


def oci(layers: list[bytes], config: bytes | None = None) -> bytes:
    """docker save 25+: gzip blobs under blobs/sha256, manifest.json pointing at them."""
    config = config or _config(layers)
    files: dict[str, bytes] = {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}'}
    names = []
    for layer in layers:
        blob = gzip.compress(layer)
        name = f"blobs/sha256/{hashlib.sha256(blob).hexdigest()}"
        files[name] = blob
        names.append(name)
    config_name = f"blobs/sha256/{hashlib.sha256(config).hexdigest()}"
    files[config_name] = config
    files["index.json"] = b'{"schemaVersion":2,"manifests":[]}'
    files["manifest.json"] = json.dumps(
        [{"Config": config_name, "RepoTags": ["myenv:v1"], "Layers": names}]
    ).encode()
    return tar_bytes(files)


def _staged(tmp_path: Path, data: bytes, chunk: int = 1000) -> ChunkReader:
    for i in range(0, len(data), chunk):
        (tmp_path / f"{i // chunk:06d}").write_bytes(data[i : i + chunk])
    return ChunkReader(tmp_path, chunk, len(data))


def _run(tmp_path: Path, data: bytes, fmt: str) -> tuple[archive.ImportResult, FakeRegistry]:
    registry = FakeRegistry()
    target = RegistryTarget(host="reg.test", repository=REPO, username="robot$x", password="s")
    with RegistryClient(target, transport=registry.transport()) as client:
        result = import_archive(
            _staged(tmp_path, data), fmt, client, "v1", max_unpacked_bytes=1 << 40
        )
    return result, registry


def _pulled_tars(registry: FakeRegistry) -> list[bytes]:
    """What a runtime would unpack: every layer of the pushed manifest, decompressed."""
    manifest = registry.manifest(REPO, "v1")
    return [zlib.decompress(registry.blobs[x["digest"]], 47) for x in manifest["layers"]]


@pytest.fixture(autouse=True)
def _small_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Members above 1 KiB stream through the push path, as multi-GB layers do."""
    monkeypatch.setattr(archive, "SMALL_MEMBER_BYTES", 1024)


class TestLayouts:
    def test_classic_tar_compresses_and_round_trips(self, tmp_path: Path):
        layers = [layer_tar(1), layer_tar(2)]
        result, registry = _run(tmp_path, classic(layers), "tar")
        assert _pulled_tars(registry) == layers
        manifest = registry.manifest(REPO, "v1")
        assert {x["mediaType"] for x in manifest["layers"]} == {archive.MT_LAYER_GZIP}
        assert result.digest == _sha(registry.manifests[(REPO, "v1")])
        assert result.dev_ready is True
        assert result.exposed_ports == ["22/tcp", "8888/tcp"]
        assert result.unpacked_bytes == sum(len(x) for x in layers)
        assert registry.patches > 0

    def test_oci_layout_pushes_gzip_blobs_as_they_are(self, tmp_path: Path):
        layers = [layer_tar(3), layer_tar(4)]
        data = oci(layers)
        _result, registry = _run(tmp_path, data, "tar")
        names = json.loads(_untar(data)["manifest.json"])[0]["Layers"]
        pushed = [x["digest"] for x in registry.manifest(REPO, "v1")["layers"]]
        assert pushed == [f"sha256:{n.rsplit('/', 1)[1]}" for n in names]
        assert _pulled_tars(registry) == layers

    def test_tar_gz_and_both_zip_shapes(self, tmp_path: Path):
        layers = [layer_tar(5)]
        save = classic(layers)
        for i, (data, fmt) in enumerate(
            [
                (gzip.compress(save), "tar.gz"),
                (_zip({"myenv.tar": save}), "zip"),
                (_zip(_untar(save, prefix="myenv/")), "zip"),
            ]
        ):
            d = tmp_path / str(i)
            d.mkdir()
            _result, registry = _run(d, data, fmt)
            assert _pulled_tars(registry) == layers, fmt

    def test_symlinked_layer_is_followed(self, tmp_path: Path):
        layers = [layer_tar(6), layer_tar(7)]
        shared = [layers[0], layers[1], layers[0]]
        result, registry = _run(tmp_path, classic(shared, share_last=True), "tar")
        assert _pulled_tars(registry) == shared
        assert result.layers == 3

    def test_non_dev_image_is_marked(self, tmp_path: Path):
        layers = [layer_tar(8)]
        config = _config(layers, config={"Cmd": ["python", "serve.py"]})
        result, _ = _run(tmp_path, classic(layers, config), "tar")
        assert result.dev_ready is False and result.cmd == ["python", "serve.py"]


class TestRejections:
    @pytest.mark.parametrize(
        ("mutate", "key"),
        [
            (lambda layers: _config(layers, os="windows"), "notLinux"),
            (lambda layers: _config(layers, architecture="s390x"), "archUnsupported"),
            (
                lambda layers: _config(
                    layers, rootfs={"type": "layers", "diff_ids": [_sha(b"other")]}
                ),
                "layerMismatch",
            ),
        ],
    )
    def test_config_problems(self, tmp_path: Path, mutate: Any, key: str):
        layers = [layer_tar(9)]
        with pytest.raises(ImportFailure) as exc:
            _run(tmp_path, classic(layers, mutate(layers)), "tar")
        assert exc.value.key == key

    def test_two_images_in_one_archive(self, tmp_path: Path):
        layers = [layer_tar(10)]
        save = classic(layers)
        files = _untar(save)
        entry = json.loads(files["manifest.json"])[0]
        files["manifest.json"] = json.dumps([entry, entry]).encode()
        with pytest.raises(ImportFailure) as exc:
            _run(tmp_path, tar_bytes(files), "tar")
        assert exc.value.key == "multipleImages"

    def test_plain_tar_and_damaged_gzip(self, tmp_path: Path):
        with pytest.raises(ImportFailure) as exc:
            _run(tmp_path, tar_bytes({"etc/hosts": b"127.0.0.1"}), "tar")
        assert exc.value.key == "notDockerArchive"
        broken = gzip.compress(classic([layer_tar(11)]))[:-200]
        (tmp_path / "b").mkdir()
        with pytest.raises(ImportFailure) as exc:
            _run(tmp_path / "b", broken, "tar.gz")
        assert exc.value.key == "corrupt"


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _untar(data: bytes, prefix: str = "") -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            f = tar.extractfile(member)
            if f is not None:
                out[prefix + member.name] = f.read()
    return out
