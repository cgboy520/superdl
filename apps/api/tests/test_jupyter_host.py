"""实例 Jupyter 的主机名/origin 与端点 slug 反解:入场 URL / HTTPRoute hostname /
JUPYTER_ALLOW_ORIGIN 同一口径。"""

import base64

import pytest

from app.core.config import Settings, get_settings
from app.modules.orchestrator.models import Instance
from app.modules.orchestrator.service import _new_jupyter_ticket, jupyter_host, jupyter_origin
from app.modules.services.service import endpoint_slug_from_host


def _settings(**overrides):
    base = {
        "_env_file": None,
        "environment": "test",
        "jupyter_domain_suffix": "app.superdl.cn",
        "config_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
    }
    base.update(overrides)
    return Settings(**base)


def test_prefix_makes_one_level_host_under_shared_zone():
    s = _settings(jupyter_domain_suffix="xiaocg.xyz", jupyter_host_prefix="jupyter-")
    assert jupyter_host("abc-123", s) == "jupyter-abc-123.xiaocg.xyz"


class TestOrigin:
    """端口只出现在 origin,不出现在主机名。"""

    def test_default_port_stays_out_of_the_url(self):
        assert jupyter_origin("abc-123", _settings()) == "https://abc-123.app.superdl.cn"

    def test_non_default_port_is_carried(self):
        s = _settings(
            jupyter_domain_suffix="xiaocg.xyz",
            jupyter_host_prefix="jupyter-",
            jupyter_url_port=8443,
        )
        assert jupyter_origin("abc-123", s) == "https://jupyter-abc-123.xiaocg.xyz:8443"
        # 主机名不带端口(HTTPRoute hostname)
        assert jupyter_host("abc-123", s) == "jupyter-abc-123.xiaocg.xyz"

    def test_entry_ticket_carries_the_port(self, monkeypatch):
        """票据 URL 带端口。"""
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
    """两类入口按端口分(config.jupyter_url_port)、后缀相同时,靠 svc- 前缀区分。"""

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
