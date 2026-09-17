"""Identity verification (KYC) providers behind one protocol; only Aliyun's three-factor mobile
check is implemented. Identity numbers are stored masked plus as a keyed digest, never in clear."""

from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.aliyun import rpc_call
from app.core.platform_config import RuntimeConfig, get_runtime_config


@dataclass(frozen=True)
class KycSubject:
    """What a provider gets to verify; fields it does not use stay None."""

    user_id: int
    full_name: str
    identity_number: str | None
    phone: str | None
    email: str | None
    country: str


@dataclass(frozen=True)
class KycResult:
    """`identity_key` is the value the platform digests for the per-identity account cap."""

    verified: bool
    provider: str
    ref: str | None = None
    identity_key: str | None = None


class KycError(RuntimeError):
    """Channel failure; callers answer 502."""


class KycRegionUnsupported(KycError):
    """The provider cannot verify this subject (e.g. it needs a +86 phone); callers answer 400."""


class KycProvider(Protocol):
    name: str

    async def verify(self, subject: KycSubject) -> KycResult:
        """Raises KycError on channel failure; a mismatch is a `verified=False` result."""
        ...


class AliyunMobile3Provider:
    """Aliyun real-person authentication, mobile three-factor check (name + ID number + +86
    phone). BizCode 1 = match, 2 = mismatch, 3 = no record."""

    name = "aliyun_mobile3"
    ENDPOINT = "https://cloudauth.aliyuncs.com/"

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._ak = access_key_id
        self._secret = access_key_secret
        self._transport = transport

    def request_params(self, name: str, id_number: str, phone: str) -> dict[str, str]:
        """Mobile3MetaSimpleVerify business parameters (national 11-digit phone)."""
        return {
            "Action": "Mobile3MetaSimpleVerify",
            "Version": "2019-03-07",
            "RegionId": "cn-hangzhou",
            "ParamType": "normal",
            "UserName": name,
            "IdentifyNum": id_number,
            "Mobile": phone,
        }

    async def verify(self, subject: KycSubject) -> KycResult:
        if not subject.identity_number or not subject.phone or not subject.phone.startswith("+86"):
            raise KycRegionUnsupported(
                "Aliyun Mobile3MetaSimpleVerify needs an ID number and a +86 phone on the account"
            )
        body = await rpc_call(
            self.ENDPOINT,
            self.request_params(subject.full_name, subject.identity_number, subject.phone[3:]),
            access_key_id=self._ak,
            access_key_secret=self._secret,
            transport=self._transport,
            error_cls=KycError,
        )
        if body.get("Code") != "200":
            raise KycError(f"kyc rejected: {body.get('Code')} {body.get('Message')}")
        biz_code = (body.get("ResultObject") or {}).get("BizCode")
        if biz_code not in ("1", "2", "3"):
            raise KycError(f"kyc unexpected BizCode: {biz_code}")
        request_id = body.get("RequestId")
        return KycResult(
            verified=biz_code == "1",
            provider=self.name,
            ref=str(request_id) if request_id else None,
            identity_key=subject.identity_number,
        )


_provider: KycProvider | None = None


def set_kyc_provider(provider: KycProvider | None) -> None:
    """Test seam; None restores construction from config."""
    global _provider
    _provider = provider


def build_kyc_provider(cfg: RuntimeConfig) -> KycProvider:
    """Provider for the effective `kyc_provider`; incomplete credentials raise KycError."""
    if not (cfg.real_name_access_key_id and cfg.real_name_access_key_secret):
        raise KycError("Aliyun identity-verification credentials not configured (real_name_* keys)")
    return AliyunMobile3Provider(cfg.real_name_access_key_id, cfg.real_name_access_key_secret)


async def get_kyc_provider(session: AsyncSession) -> KycProvider:
    if _provider is not None:
        return _provider
    return build_kyc_provider(await get_runtime_config(session))


def mask_identity(identity: str) -> str:
    """First 4 + last 2 characters kept, the rest starred."""
    return f"{identity[:4]}{'*' * (len(identity) - 6)}{identity[-2:]}"


def mask_id_name(name: str) -> str:
    """First character kept, the rest starred; one character or empty → one star."""
    if len(name) <= 1:
        return "*"
    return name[0] + "*" * (len(name) - 1)
