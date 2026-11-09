"""Staged uploads on the shared upload volume (CephFS, mounted by the API and the image-ops
worker at SUPERDL_IMAGE_UPLOAD_DIR): <root>/<user id>/<image uuid>/<chunk index>. A chunk is
written to a temporary name and renamed into place, so a present chunk file is always whole."""

import os
import shutil
import time
from datetime import timedelta
from pathlib import Path

from app.core.config import get_settings

CHUNK_BYTES = 8 * 1024 * 1024
UPLOAD_TTL = timedelta(hours=24)


def upload_root() -> Path:
    return Path(get_settings().image_upload_dir)


def staging_dir(user_id: int, image_uuid: str) -> Path:
    return upload_root() / str(user_id) / image_uuid


def expected_chunk_size(index: int, chunk_bytes: int, total: int) -> int:
    return min(chunk_bytes, total - index * chunk_bytes)


def write_chunk(directory: Path, index: int, data: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{index:06d}.part"
    tmp.write_bytes(data)
    os.replace(tmp, directory / f"{index:06d}")


def remove_dir(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def stale_dirs(keep: set[str], older_than: timedelta) -> list[Path]:
    """Upload directories no live upload owns, untouched for older_than."""
    root = upload_root()
    if not root.is_dir():
        return []
    cutoff = time.time() - older_than.total_seconds()
    out: list[Path] = []
    for user_dir in root.iterdir():
        if not user_dir.is_dir():
            continue
        out.extend(
            d
            for d in user_dir.iterdir()
            if d.is_dir() and d.name not in keep and d.stat().st_mtime < cutoff
        )
    return out
