"""Worker entry point components: fixed-length worker_id, metrics Bearer gate, scheduled-job
duration observation."""

# pyright: reportPrivateUsage=false

import hashlib
from typing import Any

from app.workers.main import (
    MAX_WORKER_ID_LEN,
    _metrics_wsgi_app,
    _timed_job,
    make_worker_id,
)


def _call_wsgi(app: Any, authorization: str | None) -> tuple[str, bytes]:
    environ: dict[str, Any] = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "QUERY_STRING": "",
        "SERVER_NAME": "test",
        "SERVER_PORT": "9000",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": None,
        "wsgi.errors": None,
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }
    if authorization is not None:
        environ["HTTP_AUTHORIZATION"] = authorization
    status_line: list[str] = []

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        status_line.append(status)

    body = b"".join(app(environ, start_response))
    return status_line[0], body


class TestMakeWorkerId:
    """Fixed-length worker_id (locked_by column width 128)."""

    def test_short_hostname_passes_through(self, monkeypatch):
        monkeypatch.setattr("app.workers.main.socket.gethostname", lambda: "pod-abc")
        monkeypatch.setattr("app.workers.main.os.getpid", lambda: 42)
        assert make_worker_id() == "pod-abc-42"

    def test_long_hostname_truncated_with_digest(self, monkeypatch):
        long_name = "superdl-worker-tenant-mgr-" + "x" * 100
        monkeypatch.setattr("app.workers.main.socket.gethostname", lambda: long_name)
        monkeypatch.setattr("app.workers.main.os.getpid", lambda: 7)
        wid = make_worker_id()
        assert len(wid) <= MAX_WORKER_ID_LEN
        assert wid.endswith("-7")
        assert hashlib.sha256(long_name.encode()).hexdigest()[:8] in wid

    def test_truncated_ids_stay_unique_across_same_prefix_hosts(self, monkeypatch):
        """Long Pod names with the same prefix + the same pid do not collide on worker_id."""
        monkeypatch.setattr("app.workers.main.os.getpid", lambda: 1)
        prefix = "superdl-worker-tenant-mgr-7f9c8d4b5"
        monkeypatch.setattr("app.workers.main.socket.gethostname", lambda: prefix + "a" * 100)
        wid_a = make_worker_id()
        monkeypatch.setattr("app.workers.main.socket.gethostname", lambda: prefix + "b" * 100)
        assert wid_a != make_worker_id()


class TestWorkerMetricsAuth:
    """The worker /metrics shares the SUPERDL_METRICS_TOKEN Bearer gate with the API."""

    def test_rejects_without_token_header(self):
        status, _ = _call_wsgi(_metrics_wsgi_app("s3cret"), None)
        assert status.startswith("401")

    def test_rejects_wrong_token(self):
        status, _ = _call_wsgi(_metrics_wsgi_app("s3cret"), "Bearer wrong")
        assert status.startswith("401")

    def test_accepts_correct_token(self):
        status, body = _call_wsgi(_metrics_wsgi_app("s3cret"), "Bearer s3cret")
        assert status.startswith("200")
        assert b"superdl_" in body or b"go_" in body or b"python_" in body

    def test_no_token_configured_is_open(self):
        status, _ = _call_wsgi(_metrics_wsgi_app(None), None)
        assert status.startswith("200")


class TestTimedJob:
    async def test_slow_tick_warns(self):
        """A round above 80 % of the period logs a warning."""
        from structlog.testing import capture_logs

        async def slow() -> None:
            import asyncio

            await asyncio.sleep(0.05)

        with capture_logs() as logs:
            await _timed_job("t_slow", slow, 0.01)()
        assert any(e.get("event") == "scheduled_tick_slow" for e in logs)
