"""实例 Jupyter 主机名拼接:前缀 + uuid + 后缀,三处调用同一口径。

挂了说明:入场 URL / Ingress host / JUPYTER_ALLOW_ORIGIN 会各拼各的,前缀配置不再对齐。
"""

import base64

from app.core.config import Settings
from app.modules.orchestrator.service import jupyter_host


def _settings(**overrides):
    base = {
        "_env_file": None,
        "environment": "test",
        "jupyter_domain_suffix": "app.superdl.cn",
        "config_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
    }
    base.update(overrides)
    return Settings(**base)


def test_default_no_prefix():
    assert jupyter_host("abc-123", _settings()) == "abc-123.app.superdl.cn"


def test_prefix_makes_one_level_host_under_shared_zone():
    s = _settings(jupyter_domain_suffix="xiaocg.xyz", jupyter_host_prefix="superdl-")
    assert jupyter_host("abc-123", s) == "superdl-abc-123.xiaocg.xyz"
