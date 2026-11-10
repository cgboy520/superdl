import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

ImageStatus = Literal["uploading", "importing", "ready", "failed", "deleting"]
ArchiveFormat = Literal["tar", "tar.gz", "zip"]

_REPO = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FILENAME_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f/\\]")


def _repo(value: str) -> str:
    if not _REPO.match(value):
        raise ValueError("lowercase letters and digits, separated by single . _ or -")
    return value


def _tag(value: str) -> str:
    if not _TAG.match(value):
        raise ValueError("letters, digits, . _ - ; starting with a letter, digit or _")
    return value


def _filename(value: str) -> str:
    value = value.strip()
    if not value or _FILENAME_FORBIDDEN.search(value):
        raise ValueError("a plain file name")
    return value


def archive_format_of(filename: str) -> ArchiveFormat | None:
    lowered = filename.lower()
    if lowered.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if lowered.endswith(".tar"):
        return "tar"
    if lowered.endswith(".zip"):
        return "zip"
    return None


RepoName = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_repo)]
TagName = Annotated[str, Field(min_length=1, max_length=128), AfterValidator(_tag)]


class CustomImageCreate(BaseModel):
    """Start an upload: the archive's name and size, and the sha256 of its first chunk (a resumed
    upload must present the same file)."""

    name: RepoName
    tag: TagName = "latest"
    filename: Annotated[str, Field(max_length=255), AfterValidator(_filename)]
    size_bytes: int = Field(ge=1)
    first_chunk_sha256: str = Field(pattern=_SHA256.pattern)
    description: str | None = Field(default=None, max_length=200)
    shared_with_org: bool = False


class CustomImageUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=200)
    shared_with_org: bool | None = None


class CustomImageOut(BaseModel):
    """owned = the caller uploaded it; otherwise a tenant member shared it (owner_label). The
    upload fields matter while uploading: received_bitmap is base64, bit i = chunk i."""

    uuid: str
    name: str
    tag: str
    description: str | None
    status: ImageStatus
    owned: bool
    owner_label: str | None = None
    shared_with_org: bool
    filename: str
    archive_format: ArchiveFormat
    upload_bytes: int
    chunk_bytes: int
    chunks_total: int
    chunks_received: int
    received_bitmap: str | None = None
    upload_expires_at: datetime
    progress_bytes: int
    error_key: str | None
    error_detail: str | None
    image_ref: str | None
    size_bytes: int | None
    unpacked_bytes: int | None
    architecture: str | None
    os: str | None
    entrypoint: list[str] | None
    cmd: list[str] | None
    exposed_ports: list[str] | None
    dev_ready: bool
    layers: int | None
    created_at: datetime
    updated_at: datetime


class ChunkAck(BaseModel):
    chunks_received: int
    chunks_total: int


class AdminCustomImageOut(CustomImageOut):
    user_id: int


class CustomImageUsageOut(BaseModel):
    """One user's quota usage: images and bytes as the upload quota counts them."""

    user_id: int
    images: int
    bytes: int


class CustomImageUsageListOut(BaseModel):
    """Per-user limits (policy parameters) and the users with images, largest first."""

    max_images: int
    max_bytes: int
    users: list[CustomImageUsageOut]
