"""Push client for the platform registry (Harbor, OCI distribution API v2): bearer-token or basic
auth, chunked blob uploads whose digest is computed while streaming, manifest PUT, and artifact
deletion through the Harbor API. Synchronous (runs in a worker thread next to the archive
reader); the transport parameter injects a mock in tests."""

import base64
import hashlib
import re
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass

import httpx

from app.core.registry import ssl_verify

UPLOAD_CHUNK_BYTES = 16 * 1024 * 1024
_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
_CHALLENGE = re.compile(r'(\w+)="([^"]*)"')


class RegistryPushError(Exception):
    """A registry call failed; status is the HTTP status when there was a response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RegistryTarget:
    host: str
    repository: str
    username: str
    password: str
    ca_pem: str = ""


@dataclass(frozen=True)
class PushedBlob:
    digest: str
    size: int


def _challenge(header: str) -> tuple[str, dict[str, str]]:
    scheme, _, rest = header.partition(" ")
    return scheme.lower(), dict(_CHALLENGE.findall(rest))


class RegistryClient:
    def __init__(
        self, target: RegistryTarget, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.target = target
        self._base = f"https://{target.host}"
        self._client = httpx.Client(
            timeout=_TIMEOUT,
            verify=ssl_verify(target.ca_pem),
            transport=transport,
            follow_redirects=False,
        )
        self._auth_header: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RegistryClient":
        return self

    def __exit__(self, *exc: object) -> None:
        del exc
        self.close()

    # ------------------------------------------------------------------ auth

    def _authenticate(self) -> None:
        probe = self._client.get(f"{self._base}/v2/")
        if probe.status_code == 200:
            self._auth_header = None
            return
        header = probe.headers.get("www-authenticate", "")
        scheme, params = _challenge(header)
        basic = httpx.BasicAuth(self.target.username, self.target.password)
        if scheme == "basic":
            pair = f"{self.target.username}:{self.target.password}".encode()
            self._auth_header = f"Basic {base64.b64encode(pair).decode()}"
            return
        if scheme != "bearer" or "realm" not in params:
            raise RegistryPushError(f"unsupported registry auth challenge: {header[:120]}")
        scope = f"repository:{self.target.repository}:pull,push"
        token = self._client.get(
            params["realm"],
            params={"service": params.get("service", ""), "scope": scope},
            auth=basic,
        )
        if token.status_code != 200:
            raise RegistryPushError("registry token request refused", status=token.status_code)
        body = token.json()
        value = body.get("token") or body.get("access_token")
        if not value:
            raise RegistryPushError("registry token response without a token")
        self._auth_header = f"Bearer {value}"

    def _send(
        self,
        method: str,
        url: str | httpx.URL,
        *,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """One request with the current credentials; a 401 re-authenticates once (tokens expire
        during long uploads)."""
        for attempt in range(2):
            if self._auth_header is None and attempt == 0:
                self._authenticate()
            merged = dict(headers or {})
            if self._auth_header:
                merged["Authorization"] = self._auth_header
            resp = self._client.request(method, url, content=content, headers=merged)
            if resp.status_code != 401 or attempt == 1:
                return resp
            self._authenticate()
        raise AssertionError("unreachable")  # pragma: no cover

    def _repo_url(self, suffix: str) -> str:
        return f"{self._base}/v2/{self.target.repository}/{suffix}"

    def _location(self, resp: httpx.Response) -> httpx.URL:
        location = resp.headers.get("location")
        if not location:
            raise RegistryPushError("registry upload response without a Location")
        return httpx.URL(self._base).join(location)

    # ------------------------------------------------------------------ blobs

    def blob_exists(self, digest: str) -> bool:
        resp = self._send("HEAD", self._repo_url(f"blobs/{digest}"))
        return resp.status_code == 200

    def push_blob(self, pieces: Iterable[bytes]) -> PushedBlob:
        """Chunked upload (PATCH with Content-Range, final PUT ?digest=); the digest is the sha256
        of the bytes as streamed."""
        start = self._send("POST", self._repo_url("blobs/uploads/"))
        if start.status_code != 202:
            raise RegistryPushError("blob upload refused", status=start.status_code)
        location = self._location(start)
        digest = hashlib.sha256()
        offset = 0
        buffer = bytearray()

        def flush(data: bytes) -> None:
            nonlocal location, offset
            resp = self._send(
                "PATCH",
                location,
                content=data,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Range": f"{offset}-{offset + len(data) - 1}",
                },
            )
            if resp.status_code != 202:
                raise RegistryPushError("blob chunk refused", status=resp.status_code)
            location = self._location(resp)
            offset += len(data)

        for piece in pieces:
            digest.update(piece)
            buffer.extend(piece)
            while len(buffer) >= UPLOAD_CHUNK_BYTES:
                flush(bytes(buffer[:UPLOAD_CHUNK_BYTES]))
                del buffer[:UPLOAD_CHUNK_BYTES]
        if buffer:
            flush(bytes(buffer))
        value = f"sha256:{digest.hexdigest()}"
        done = self._send(
            "PUT", location.copy_merge_params({"digest": value}), headers={"Content-Length": "0"}
        )
        if done.status_code != 201:
            raise RegistryPushError("blob commit refused", status=done.status_code)
        return PushedBlob(digest=value, size=offset)

    def push_bytes(self, data: bytes) -> PushedBlob:
        digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if self.blob_exists(digest):
            return PushedBlob(digest=digest, size=len(data))
        return self.push_blob([data])

    # ------------------------------------------------------------------ manifests

    def put_manifest(self, reference: str, body: bytes, media_type: str) -> str:
        resp = self._send(
            "PUT",
            self._repo_url(f"manifests/{reference}"),
            content=body,
            headers={"Content-Type": media_type},
        )
        if resp.status_code not in (200, 201):
            raise RegistryPushError("manifest refused", status=resp.status_code)
        return f"sha256:{hashlib.sha256(body).hexdigest()}"


def delete_harbor_artifact(
    target: RegistryTarget, digest: str, *, transport: httpx.BaseTransport | None = None
) -> None:
    """Delete one artifact through the Harbor API (robot with artifact delete permission); a
    missing artifact or repository counts as success."""
    project, _, repo = target.repository.partition("/")
    repo_enc = urllib.parse.quote(urllib.parse.quote(repo, safe=""), safe="")
    url = (
        f"https://{target.host}/api/v2.0/projects/{project}/repositories/{repo_enc}"
        f"/artifacts/{digest}"
    )
    with httpx.Client(
        timeout=_TIMEOUT, verify=ssl_verify(target.ca_pem), transport=transport
    ) as client:
        resp = client.delete(url, auth=(target.username, target.password))
    if resp.status_code not in (200, 404):
        raise RegistryPushError("artifact delete refused", status=resp.status_code)
