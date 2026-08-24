"""worker 入口组件:metrics Bearer 门禁与定时任务耗时观测(不启动整个 worker 进程)。"""

from typing import Any

from prometheus_client import REGISTRY

from app.workers.main import _metrics_wsgi_app, _timed_job


class TestScheduledJobsManifest:
    """register_scheduled_jobs 任务清单快照:新增任务未登记/误删任务
    未同步本断言即红 —— 防静默丢任务(结算/对账/巡检停摆无人发现)。"""

    EXPECTED_JOB_IDS = frozenset(
        {
            "outbox_reaper",
            "reconciler",
            "hourly_settlement",
            "daily_disk_settlement",
            "fund_reconcile",
            "usage_aggregation",
            "close_expired_orders",
            "payment_reconcile",
            "cleanup_expired_rows",
            "balance_patrol",
            "prewarm_patrol",
            "node_spec_patrol",
            "node_enroll_reconciler",
            "ticket_stale_patrol",
        }
    )

    async def test_registered_jobs_manifest(self):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        from app.workers.main import register_scheduled_jobs

        scheduler = AsyncIOScheduler(timezone="UTC")
        register_scheduled_jobs(scheduler)
        scheduler.start(paused=True)  # paused:只取注册清单,不触发任何任务执行
        try:
            ids = {job.id for job in scheduler.get_jobs()}
        finally:
            scheduler.shutdown(wait=False)
        assert ids == self.EXPECTED_JOB_IDS, (
            f"定时任务清单漂移:新增未登记 {sorted(ids - self.EXPECTED_JOB_IDS)};"
            f"丢失 {sorted(self.EXPECTED_JOB_IDS - ids)}"
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

    async def test_slow_tick_warns(self):
        """单轮耗时超过周期 80% 必须打 warning(coalesce/misfire 静默丢轮的前兆)。
        用 structlog 事件捕获而非 capsys:日志管道在套件早期已绑定原始 stdout,
        全量跑时 capsys 抓不到(顺序相关 flake)。"""
        from structlog.testing import capture_logs

        async def slow() -> None:
            import asyncio

            await asyncio.sleep(0.05)

        with capture_logs() as logs:
            await _timed_job("t_slow", slow, 0.01)()  # 周期 10ms,必然超 80%
        assert any(e.get("event") == "scheduled_tick_slow" for e in logs)

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
