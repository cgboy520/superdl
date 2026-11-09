"""Import one `docker save` archive (.tar, .tar.gz / .tgz, .zip) into the registry, streaming it
once from the staged upload. Synchronous; runs in a worker thread.

Accepted inputs: the classic layout (`<id>/layer.tar`, `<config>.json`) and the OCI layout of
Docker 25+ (`blobs/sha256/<hex>`), both described by `manifest.json` with exactly one image; a zip
holds either one such tar or the unpacked tree. Members are never written to disk: large regular
files are pushed as layer candidates while streaming, small files are kept in memory until
`manifest.json` says what they are. Uncompressed layers are gzip-compressed on the way, gzip
layers are pushed as they are, zstd layers as they are without diff-id verification; every
other diff id must match the image config. The result is an OCI image manifest under the tag.
"""

import hashlib
import io
import json
import posixpath
import tarfile
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from app.core.registry_push import RegistryClient

SMALL_MEMBER_BYTES = 4 * 1024 * 1024
SMALL_TOTAL_BYTES = 256 * 1024 * 1024
MAX_LAYERS = 127
READ_BYTES = 1024 * 1024
DEV_ENTRYPOINT = ["/usr/local/bin/superdl-entrypoint.sh"]
SUPPORTED_ARCHES = frozenset({"amd64", "arm64"})

MT_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
MT_CONFIG = "application/vnd.oci.image.config.v1+json"
MT_LAYER_GZIP = "application/vnd.oci.image.layer.v1.tar+gzip"
MT_LAYER_ZSTD = "application/vnd.oci.image.layer.v1.tar+zstd"

_GZIP_MAGIC = b"\x1f\x8b"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


class ImportFailure(Exception):
    """The archive is not an importable Docker image; key is the user-facing message key
    suffix (customImages.import.<key>)."""

    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(f"{key}: {detail}" if detail else key)
        self.key = key
        self.detail = detail[:500]


@dataclass
class ImportResult:
    digest: str
    size_bytes: int
    unpacked_bytes: int
    architecture: str
    os: str
    entrypoint: list[str] | None
    cmd: list[str] | None
    exposed_ports: list[str]
    dev_ready: bool
    layers: int


@dataclass
class _Layer:
    digest: str
    size: int
    media_type: str
    diff_id: str | None
    unpacked: int


@dataclass
class _Scan:
    small: dict[str, bytes] = field(default_factory=dict)
    small_total: int = 0
    pushed: dict[str, _Layer] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)


class ChunkReader(io.RawIOBase):
    """Seekable read-only view over the numbered chunk files of one upload."""

    def __init__(self, directory: Path, chunk_bytes: int, total: int) -> None:
        super().__init__()
        self._dir = directory
        self._chunk = chunk_bytes
        self._total = total
        self._pos = 0
        self.consumed = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._total}[whence]
        self._pos = max(0, min(self._total, base + offset))
        return self._pos

    def readinto(self, buffer: Any) -> int:
        view = memoryview(buffer).cast("B")
        if self._pos >= self._total or not len(view):
            return 0
        index, offset = divmod(self._pos, self._chunk)
        with (self._dir / f"{index:06d}").open("rb") as f:
            f.seek(offset)
            data = f.read(min(len(view), self._chunk - offset, self._total - self._pos))
        view[: len(data)] = data
        self._pos += len(data)
        self.consumed = max(self.consumed, self._pos)
        return len(data)


def _norm(name: str) -> str:
    return posixpath.normpath(name.lstrip("./")).lstrip("/")


class _LayerStream:
    """Pieces of one layer blob as pushed, plus the digest and size of its uncompressed tar."""

    def __init__(self, first: bytes, f: IO[bytes]) -> None:
        self._first = first
        self._f = f
        self.diff = hashlib.sha256()
        self.unpacked = 0
        self.verify = not first.startswith(_ZSTD_MAGIC)
        if first.startswith(_ZSTD_MAGIC):
            self.media_type, self._mode = MT_LAYER_ZSTD, "as-is"
        elif first.startswith(_GZIP_MAGIC):
            self.media_type, self._mode = MT_LAYER_GZIP, "gzip"
        else:
            self.media_type, self._mode = MT_LAYER_GZIP, "compress"

    def _raw(self) -> Iterator[bytes]:
        if self._first:
            yield self._first
        while piece := self._f.read(READ_BYTES):
            yield piece

    def _as_is(self) -> Iterator[bytes]:
        for piece in self._raw():
            self.unpacked += len(piece)
            yield piece

    def _gzip(self) -> Iterator[bytes]:
        inflater = zlib.decompressobj(wbits=47)
        for piece in self._raw():
            self._count(inflater.decompress(piece))
            yield piece
        self._count(inflater.flush())

    def _compress(self) -> Iterator[bytes]:
        deflater = zlib.compressobj(level=6, wbits=31)
        for piece in self._raw():
            self._count(piece)
            out = deflater.compress(piece)
            if out:
                yield out
        yield deflater.flush()

    def _count(self, raw: bytes) -> None:
        self.diff.update(raw)
        self.unpacked += len(raw)

    def pieces(self) -> Iterator[bytes]:
        return {"as-is": self._as_is, "gzip": self._gzip, "compress": self._compress}[self._mode]()


def _push_layer(client: RegistryClient, f: IO[bytes]) -> _Layer:
    stream = _LayerStream(f.read(4), f)
    pushed = client.push_blob(stream.pieces())
    diff_id = f"sha256:{stream.diff.hexdigest()}" if stream.verify else None
    return _Layer(pushed.digest, pushed.size, stream.media_type, diff_id, stream.unpacked)


def _scan_member(client: RegistryClient, scan: _Scan, name: str, size: int, f: IO[bytes]) -> None:
    if size <= SMALL_MEMBER_BYTES:
        scan.small_total += size
        if scan.small_total > SMALL_TOTAL_BYTES:
            raise ImportFailure("notDockerArchive", "too many small files for an image archive")
        scan.small[name] = f.read()
        return
    scan.pushed[name] = _push_layer(client, f)


def _scan_tar(client: RegistryClient, stream: IO[bytes], scan: _Scan) -> None:
    mode = "r|gz" if _peek(stream).startswith(_GZIP_MAGIC) else "r|"
    try:
        with tarfile.open(fileobj=stream, mode=mode) as tar:
            for member in tar:
                name = _norm(member.name)
                if member.issym() or member.islnk():
                    target = member.linkname
                    if member.issym():
                        target = posixpath.join(posixpath.dirname(name), target)
                    scan.links[name] = _norm(target)
                    continue
                if not member.isfile():
                    continue
                f = tar.extractfile(member)
                if f is not None:
                    _scan_member(client, scan, name, member.size, f)
    except (tarfile.TarError, EOFError, zlib.error) as exc:
        raise ImportFailure("corrupt", str(exc)) from exc


def _peek(stream: IO[bytes]) -> bytes:
    peek = getattr(stream, "peek", None)
    if callable(peek):
        head = peek(4)
        if isinstance(head, bytes) and len(head) >= 2:
            return head[:4]
    pos = stream.tell()
    head = stream.read(4)
    stream.seek(pos)
    return head


def _zip_root(names: list[str]) -> str | None:
    """Directory holding manifest.json inside an unpacked tree (the root or one folder)."""
    roots = sorted(posixpath.dirname(n) for n in names if posixpath.basename(n) == "manifest.json")
    return roots[0] if roots else None


def _scan_zip(client: RegistryClient, reader: IO[bytes], scan: _Scan) -> None:
    try:
        zf = zipfile.ZipFile(reader)
    except zipfile.BadZipFile as exc:
        raise ImportFailure("corrupt", str(exc)) from exc
    with zf:
        files = [
            i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")
        ]
        names = [_norm(i.filename) for i in files]
        archives = [i for i in files if i.filename.lower().endswith((".tar", ".tar.gz", ".tgz"))]
        root = _zip_root(names)
        if root is None and len(archives) == 1:
            with zf.open(archives[0]) as inner:
                _scan_tar(client, inner, scan)
            return
        if root is None:
            raise ImportFailure("notDockerArchive", "no manifest.json and no single image tar")
        for info, name in zip(files, names, strict=True):
            rel = name[len(root) + 1 :] if root else name
            if root and not name.startswith(f"{root}/"):
                continue
            with zf.open(info) as f:
                _scan_member(client, scan, rel, info.file_size, f)


def _resolve(scan: _Scan, name: str) -> str:
    seen = 0
    name = _norm(name)
    while name in scan.links:
        name = scan.links[name]
        seen += 1
        if seen > 8:
            raise ImportFailure("corrupt", "link loop")
    return name


def _manifest_entry(scan: _Scan) -> dict[str, Any]:
    raw = scan.small.get("manifest.json")
    if raw is None:
        raise ImportFailure("notDockerArchive", "manifest.json is missing")
    try:
        entries = json.loads(raw)
    except ValueError as exc:
        raise ImportFailure("notDockerArchive", "manifest.json is not JSON") from exc
    if not isinstance(entries, list) or not entries:
        raise ImportFailure("notDockerArchive", "manifest.json lists no image")
    if len(entries) != 1:
        raise ImportFailure("multipleImages", f"{len(entries)} images in one archive")
    entry = entries[0]
    if not isinstance(entry, dict) or not entry.get("Config") or not entry.get("Layers"):
        raise ImportFailure("notDockerArchive", "manifest.json entry lacks Config or Layers")
    return entry


def _image_config(scan: _Scan, entry: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    name = _resolve(scan, str(entry["Config"]))
    raw = scan.small.get(name)
    if raw is None:
        raise ImportFailure("notDockerArchive", f"config {name} is missing")
    try:
        config = json.loads(raw)
    except ValueError as exc:
        raise ImportFailure("notDockerArchive", "the image config is not JSON") from exc
    if not isinstance(config, dict):
        raise ImportFailure("notDockerArchive", "the image config is not an object")
    if config.get("os") != "linux":
        raise ImportFailure("notLinux", str(config.get("os")))
    if config.get("architecture") not in SUPPORTED_ARCHES:
        raise ImportFailure("archUnsupported", str(config.get("architecture")))
    return raw, config


def _layers(client: RegistryClient, scan: _Scan, entry: dict[str, Any]) -> list[_Layer]:
    names = [_resolve(scan, str(n)) for n in entry["Layers"]]
    if len(names) > MAX_LAYERS:
        raise ImportFailure("tooManyLayers", str(len(names)))
    out: list[_Layer] = []
    for name in names:
        layer = scan.pushed.get(name)
        if layer is None and name in scan.small:
            layer = _push_layer(client, io.BytesIO(scan.small[name]))
            scan.pushed[name] = layer
        if layer is None:
            raise ImportFailure("notDockerArchive", f"layer {name} is missing")
        out.append(layer)
    return out


def _verify(config: dict[str, Any], layers: list[_Layer]) -> None:
    diff_ids = (config.get("rootfs") or {}).get("diff_ids") or []
    if len(diff_ids) != len(layers):
        raise ImportFailure("layerMismatch", f"{len(layers)} layers, {len(diff_ids)} diff ids")
    for i, (expected, layer) in enumerate(zip(diff_ids, layers, strict=True)):
        if layer.diff_id is not None and layer.diff_id != expected:
            raise ImportFailure("layerMismatch", f"layer {i} content does not match its diff id")


def _strings(value: Any) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    return None


def import_archive(
    reader: io.RawIOBase,
    archive_format: str,
    client: RegistryClient,
    tag: str,
    *,
    max_unpacked_bytes: int,
) -> ImportResult:
    scan = _Scan()
    if archive_format == "zip":
        _scan_zip(client, io.BufferedReader(reader, buffer_size=READ_BYTES), scan)
    else:
        _scan_tar(client, io.BufferedReader(reader, buffer_size=READ_BYTES), scan)
    entry = _manifest_entry(scan)
    config_raw, config = _image_config(scan, entry)
    layers = _layers(client, scan, entry)
    _verify(config, layers)
    unpacked = sum(layer.unpacked for layer in layers)
    if unpacked > max_unpacked_bytes:
        raise ImportFailure("tooLarge", f"{unpacked} bytes unpacked")
    config_blob = client.push_bytes(config_raw)
    manifest = {
        "schemaVersion": 2,
        "mediaType": MT_MANIFEST,
        "config": {"mediaType": MT_CONFIG, "digest": config_blob.digest, "size": len(config_raw)},
        "layers": [
            {"mediaType": layer.media_type, "digest": layer.digest, "size": layer.size}
            for layer in layers
        ],
    }
    body = json.dumps(manifest, separators=(",", ":")).encode()
    digest = client.put_manifest(tag, body, MT_MANIFEST)
    inner = config.get("config") or {}
    entrypoint = _strings(inner.get("Entrypoint"))
    return ImportResult(
        digest=digest,
        size_bytes=len(config_raw) + sum(layer.size for layer in layers),
        unpacked_bytes=unpacked,
        architecture=str(config["architecture"]),
        os=str(config["os"]),
        entrypoint=entrypoint,
        cmd=_strings(inner.get("Cmd")),
        exposed_ports=sorted((inner.get("ExposedPorts") or {}).keys()),
        dev_ready=entrypoint == DEV_ENTRYPOINT,
        layers=len(layers),
    )
