"""In-memory OCI distribution registry (bearer token flow, chunked uploads, manifests) plus the
Harbor artifact delete call, as an httpx transport for push tests."""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

TOKEN = "fake-token"


@dataclass
class FakeRegistry:
    blobs: dict[str, bytes] = field(default_factory=dict)
    manifests: dict[tuple[str, str], bytes] = field(default_factory=dict)
    uploads: dict[str, bytearray] = field(default_factory=dict)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    patches: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def manifest(self, repository: str, reference: str) -> dict[str, Any]:
        return json.loads(self.manifests[(repository, reference)])

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/service/token":
            return httpx.Response(200, json={"token": TOKEN})
        if path.startswith("/api/v2.0/"):
            match = re.match(
                r"/api/v2.0/projects/([^/]+)/repositories/([^/]+)/artifacts/(.+)$", path
            )
            assert match and request.method == "DELETE"
            self.deleted.append((match.group(1), match.group(3)))
            return httpx.Response(200)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer realm="https://reg.test/service/token",'
                    'service="harbor-registry"'
                },
            )
        if path == "/v2/":
            return httpx.Response(200)
        return self._v2(request, path)

    def _v2(self, request: httpx.Request, path: str) -> httpx.Response:
        if m := re.match(r"/v2/(.+)/blobs/uploads/$", path):
            upload = uuid.uuid4().hex
            self.uploads[upload] = bytearray()
            return httpx.Response(
                202, headers={"Location": f"/v2/{m.group(1)}/blobs/uploads/{upload}?_state=x"}
            )
        if m := re.match(r"/v2/(.+)/blobs/uploads/([0-9a-f]+)$", path):
            repo, upload = m.group(1), m.group(2)
            if request.method == "PATCH":
                start = int(request.headers["content-range"].split("-")[0])
                assert start == len(self.uploads[upload]), "out-of-order chunk"
                self.uploads[upload].extend(request.content)
                self.patches += 1
                return httpx.Response(
                    202, headers={"Location": f"/v2/{repo}/blobs/uploads/{upload}?_state=y"}
                )
            data = bytes(self.uploads.pop(upload))
            digest = request.url.params["digest"]
            assert digest == f"sha256:{hashlib.sha256(data).hexdigest()}"
            self.blobs[digest] = data
            return httpx.Response(201)
        if m := re.match(r"/v2/(.+)/blobs/(sha256:[0-9a-f]+)$", path):
            return httpx.Response(200 if m.group(2) in self.blobs else 404)
        if m := re.match(r"/v2/(.+)/manifests/(.+)$", path):
            body = request.content
            for layer in [json.loads(body)["config"], *json.loads(body)["layers"]]:
                assert layer["digest"] in self.blobs, "manifest references a missing blob"
            self.manifests[(m.group(1), m.group(2))] = body
            return httpx.Response(201)
        return httpx.Response(404)
