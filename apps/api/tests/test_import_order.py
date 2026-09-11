"""进程入口的导入顺序:worker 先导 billing.edge_listener,api 先导 main。
挂了说明:模块级互相 import 形成环(如 billing.settlement 顶层导 orchestrator.service),
某个入口起不来。"""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "entry",
    [
        "import app.modules.billing.edge_listener",
        "import app.modules.billing.settlement",
        "import app.workers.main",
        "import app.main",
    ],
)
def test_entry_imports_in_fresh_interpreter(entry: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", entry],
        capture_output=True,
        text=True,
        env={"SUPERDL_ENVIRONMENT": "test", "SUPERDL_K8S_BACKEND": "fake", "PATH": "/usr/bin:/bin"},
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
