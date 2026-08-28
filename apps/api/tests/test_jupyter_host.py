"""实例 Jupyter 的主机名/origin 与端点 slug 反解 —— 域名与端口布局的单一口径。

挂了说明其中之一:
- 入场 URL / HTTPRoute hostname / JUPYTER_ALLOW_ORIGIN 三处各拼各的(前缀或端口对不齐);
- 两类入口共用一个域名后缀时,Jupyter 域名成了鉴权端点的别名。
"""

import base64

import pytest

from app.core.config import Settings, get_settings
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.service import (
    _new_jupyter_ticket,
    endpoint_slug_from_host,
    jupyter_host,
    jupyter_origin,
)


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
    s = _settings(jupyter_domain_suffix="xiaocg.xyz", jupyter_host_prefix="jupyter-")
    assert jupyter_host("abc-123", s) == "jupyter-abc-123.xiaocg.xyz"


class TestOrigin:
    """origin 是给浏览器看的,主机名是给 Gateway 与 SSH 看的 —— 端口只能出现在前者。"""

    def test_default_port_stays_out_of_the_url(self):
        assert jupyter_origin("abc-123", _settings()) == "https://abc-123.app.superdl.cn"

    def test_non_default_port_is_carried(self):
        s = _settings(
            jupyter_domain_suffix="xiaocg.xyz",
            jupyter_host_prefix="jupyter-",
            jupyter_url_port=8443,
        )
        assert jupyter_origin("abc-123", s) == "https://jupyter-abc-123.xiaocg.xyz:8443"
        # 主机名本身不许带端口:它要去当 HTTPRoute 的 hostname(带端口会被 CRD 拒收)
        assert jupyter_host("abc-123", s) == "jupyter-abc-123.xiaocg.xyz"

    def test_entry_ticket_carries_the_port(self, monkeypatch):
        """票据 URL 少一个端口号,页面能打开而内核 WebSocket 被自己的 CORS 全挡掉。"""
        monkeypatch.setenv("SUPERDL_JUPYTER_DOMAIN_SUFFIX", "xiaocg.xyz")
        monkeypatch.setenv("SUPERDL_JUPYTER_HOST_PREFIX", "jupyter-")
        monkeypatch.setenv("SUPERDL_JUPYTER_URL_PORT", "8443")
        get_settings.cache_clear()
        try:
            url = _new_jupyter_ticket(Instance(uuid="abc123"), "tok")
        finally:
            get_settings.cache_clear()
        assert url.startswith("https://jupyter-abc123.xiaocg.xyz:8443/superdl-bootstrap?")


class TestSlugParsingUnderSharedSuffix:
    """两类入口靠端口分开(见 config.jupyter_url_port)时,两个后缀是同一个字符串。

    此时后缀比对不再能把 Jupyter 域与端点域分开,把关的只剩 svc- 前缀这一条。
    """

    @pytest.fixture
    def shared_suffix(self, monkeypatch):
        monkeypatch.setenv("SUPERDL_JUPYTER_DOMAIN_SUFFIX", "xiaocg.xyz")
        monkeypatch.setenv("SUPERDL_SERVICE_DOMAIN_SUFFIX", "xiaocg.xyz")
        monkeypatch.setenv("SUPERDL_JUPYTER_HOST_PREFIX", "jupyter-")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def test_endpoint_host_still_parses(self, shared_suffix):
        assert endpoint_slug_from_host("svc-abc1234567.xiaocg.xyz") == "svc-abc1234567"

    def test_jupyter_host_is_not_an_endpoint_alias(self, shared_suffix):
        assert endpoint_slug_from_host("jupyter-0e05c3daf14a.xiaocg.xyz") is None

    def test_bare_zone_and_foreign_labels_rejected(self, shared_suffix):
        assert endpoint_slug_from_host("xiaocg.xyz") is None
        assert endpoint_slug_from_host("anything.xiaocg.xyz") is None
