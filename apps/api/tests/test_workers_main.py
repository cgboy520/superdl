"""worker 入口组件:metrics Bearer 门禁与定时任务耗时观测(不启动整个 worker 进程)。
定时任务注册清单由 test_workers_components 与组件分片表双向锁定。"""

import hashlib
from typing import Any

from app.workers.main import (
    MAX_WORKER_ID_LEN,
    OUTBOX_CONCURRENCY,
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
    """worker_id 定长化:locked_by 列宽是硬约束,超长 = claim commit 抛错 =
    该组件 outbox 整体静默停摆(任务滞留 pending,心跳/探针/死信指标全部正常)。"""

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
        # 不是纯截断:中段有全名哈希,保住同前缀 Pod 名之间的区分度
        assert hashlib.sha256(long_name.encode()).hexdigest()[:8] in wid

    def test_truncated_ids_stay_unique_across_same_prefix_hosts(self, monkeypatch):
        """同前缀长 Pod 名 + 容器内同 pid:只截前缀会撞 worker_id(挂了 = 双副本
        互相覆盖终态写,ownership 校验形同虚设)。"""
        monkeypatch.setattr("app.workers.main.os.getpid", lambda: 1)
        prefix = "superdl-worker-tenant-mgr-7f9c8d4b5"
        monkeypatch.setattr("app.workers.main.socket.gethostname", lambda: prefix + "a" * 100)
        wid_a = make_worker_id()
        monkeypatch.setattr("app.workers.main.socket.gethostname", lambda: prefix + "b" * 100)
        assert wid_a != make_worker_id()

    def test_lane_suffix_fits_locked_by_column(self, monkeypatch):
        """lane 后缀叠加后仍 ≤ locked_by 列宽 128(挂了 = 静默停摆复发)。"""
        monkeypatch.setattr("app.workers.main.socket.gethostname", lambda: "h" * 200)
        monkeypatch.setattr("app.workers.main.os.getpid", lambda: 12345)
        assert len(f"{make_worker_id()}-{OUTBOX_CONCURRENCY - 1}") <= 128


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
