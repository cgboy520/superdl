"""worker 入口组件:metrics Bearer 门禁与定时任务耗时观测(不启动整个 worker 进程)。"""

from typing import Any

from prometheus_client import REGISTRY

from app.workers.main import _metrics_wsgi_app, _timed_job


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


class TestWorkerMetricsAuth:
    """worker /metrics 与 API 同一 SUPERDL_METRICS_TOKEN Bearer 门禁。"""

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

    def test_non_ascii_header_does_not_crash(self):
        # compare_digest 收 str 遇非 ASCII 会抛 TypeError:必须先 encode(401 而非 500)
        status, _ = _call_wsgi(_metrics_wsgi_app("s3cret"), "Bearer tokén")
        assert status.startswith("401")

    def test_no_token_configured_is_open(self):
        # dev/test 未配 SUPERDL_METRICS_TOKEN 时与 API 侧一致:不要求鉴权(prod 强制配置)
        status, _ = _call_wsgi(_metrics_wsgi_app(None), None)
        assert status.startswith("200")


class TestTimedJob:
    async def test_observes_duration_histogram(self):
        before = REGISTRY.get_sample_value(
            "superdl_scheduled_tick_duration_seconds_count", {"job": "t_fast"}
        )

        async def fast() -> int:
            return 1

        assert await _timed_job("t_fast", fast, 60.0)() == 1
        after = REGISTRY.get_sample_value(
            "superdl_scheduled_tick_duration_seconds_count", {"job": "t_fast"}
        )
        assert (after or 0) == (before or 0) + 1

    async def test_slow_tick_warns(self, capsys):
        """单轮耗时超过周期 80% 必须打 warning(coalesce/misfire 静默丢轮的前兆)。"""

        async def slow() -> None:
            import asyncio

            await asyncio.sleep(0.05)

        await _timed_job("t_slow", slow, 0.01)()  # 周期 10ms,必然超 80%
        assert "scheduled_tick_slow" in capsys.readouterr().out

    async def test_exception_still_observed(self):
        import pytest

        async def boom() -> None:
            raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            await _timed_job("t_boom", boom, 60.0)()
        assert (
            REGISTRY.get_sample_value(
                "superdl_scheduled_tick_duration_seconds_count", {"job": "t_boom"}
            )
            == 1
        )
