"""各进程入口在全新解释器里能独立导入:worker 先导 billing.edge_listener,api 先导 main。
挂了说明:模块级互相 import 形成环(billing 只许经 orchestrator.queries / transitions /
statemachine / ports 访问编排,不许经 orchestrator.service),某个入口起不来。"""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "entry",
    [
        "import app.modules.billing.edge_listener",
        "import app.modules.billing.settlement",
        "import app.modules.billing.patrol",
        "import app.modules.orchestrator.queries",
        "import app.workers.main",
        "import app.main",
    ],
)
def test_entry_imports_in_fresh_interpreter(entry: str) -> None:
    env = {
        "SUPERDL_ENVIRONMENT": "test",
        "SUPERDL_K8S_BACKEND": "fake",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    # Windows 上 asyncio 初始化 winsock 需要 SYSTEMROOT
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    proc = subprocess.run(
        [sys.executable, "-c", entry],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
