"""Check in a separate interpreter that the business modules and the API / worker entry points
import."""

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
