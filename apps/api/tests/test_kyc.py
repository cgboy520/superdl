"""Identity verification: real_name_enabled switch, compliance-profile gating (form + gate), PRC ID
checksum, provider seam (fake provider / Aliyun region rule), masked storage, per-identity cap, and
the recharge / instance / disk gates."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.account.kyc import (
    AliyunMobile3Provider,
    KycResult,
    KycSubject,
    set_kyc_provider,
)
from app.modules.account.models import User
from tests.helpers import (
    as_handle,
    create_test_sku,
    fund_wallet,
    gen_ed25519_key,
    issue_code,
    seed_instance,
    seed_node_spec,
    set_platform_setting,
    user_headers_with_id,
)

VALID_ID = "110101199001011237"
OTHER_ID = "110101199001010007"


class _Provider:
    name = "fake"

    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.subjects: list[KycSubject] = []

    async def verify(self, subject: KycSubject) -> KycResult:
        self.subjects.append(subject)
        return KycResult(self.ok, "fake", ref="ref-1", identity_key=subject.identity_number)


@pytest.fixture(autouse=True)
def _cn_profile(monkeypatch):
    """The only KYC form today is the PRC ID card, so these tests run under the cn profile."""
    monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
    yield
    set_kyc_provider(None)


async def _phone_user(client: AsyncClient, sm, digits: str) -> tuple[dict[str, str], int]:
    """Register an email account that also binds a +86 phone (the three-factor check needs it)."""
    email, phone = as_handle(digits), f"+86{digits}"
    await issue_code(sm, email, "register")
    await issue_code(sm, phone, "register")
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "email_code": "123456",
            "phone": phone,
            "phone_code": "123456",
            "accept_terms": True,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {"Authorization": f"Bearer {data['access_token']}"}, data["user"]["id"]


async def _funded_phone_user(
    client: AsyncClient, sm, digits: str, amount: str = "100.00"
) -> tuple[dict[str, str], int, int]:
    headers, user_id = await _phone_user(client, sm, digits)
    key = await client.post(
        "/api/v1/ssh-keys", json={"name": "t", "public_key": gen_ed25519_key()}, headers=headers
    )
    assert key.status_code == 201, key.text
    await fund_wallet(sm, user_id, amount)
    return headers, user_id, key.json()["id"]


def _body(identity: str = VALID_ID, name: str = "张三") -> dict:  # cjk-ok
    return {"full_name": name, "identity_number": identity}


class TestSubmitKyc:
    async def test_verify_success_masks_identity_and_records_provider(self, client, sm):
        await set_platform_setting(sm, "real_name_enabled", "true")
        provider = _Provider(True)
        set_kyc_provider(provider)
        headers, user_id = await _phone_user(client, sm, "13800000160")
        resp = await client.post("/api/v1/me/kyc", json=_body(), headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["kyc_status"] == "verified"
        assert (
            provider.subjects[0].phone == "+8613800000160" and provider.subjects[0].country == "CN"
        )
        async with sm() as session:
            user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
            assert user.kyc_name == "张三"  # cjk-ok
            assert user.kyc_identity_masked == "1101************37"
            assert "199001011237" not in (user.kyc_identity_masked or "")
            assert user.kyc_provider == "fake" and user.kyc_ref == "ref-1"
            assert user.kyc_verified_at is not None and user.kyc_identity_hmac
        again = await client.post("/api/v1/me/kyc", json=_body(), headers=headers)
        assert again.status_code == 409 and again.json()["code"] == "CONFLICT"

    async def test_mismatch_rejected(self, client, sm):
        await set_platform_setting(sm, "real_name_enabled", "true")
        set_kyc_provider(_Provider(False))
        headers, _ = await _phone_user(client, sm, "13800000161")
        resp = await client.post(
            "/api/v1/me/kyc",
            json=_body(OTHER_ID, "李四"),  # cjk-ok
            headers=headers,  # cjk-ok
        )  # cjk-ok
        assert resp.status_code == 400
        assert resp.json()["code"] == "REAL_NAME_MISMATCH"

    async def test_invalid_checksum_rejected_before_the_provider(self, client, sm):
        await set_platform_setting(sm, "real_name_enabled", "true")
        provider = _Provider(True)
        set_kyc_provider(provider)
        headers, _ = await _phone_user(client, sm, "13800000168")
        resp = await client.post(
            "/api/v1/me/kyc", json=_body("110101199001011234"), headers=headers
        )
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "account.kycIdentityInvalid"
        assert provider.subjects == []

    async def test_disabled_is_409_without_touching_provider(self, client, sm):
        set_kyc_provider(_Provider(True))
        headers, _ = await _phone_user(client, sm, "13800000166")
        resp = await client.post("/api/v1/me/kyc", json=_body(), headers=headers)
        assert resp.status_code == 409
        assert resp.json()["code"] == "REAL_NAME_DISABLED"

    async def test_generic_profile_has_no_kyc_form(self, client, sm, monkeypatch):
        """compliance_profile=none: 409 kycNotAvailable, and the recharge gate is inert even when
        real_name_required_for_recharge is on."""
        monkeypatch.setattr(get_settings(), "compliance_profile", None)
        await set_platform_setting(sm, "real_name_enabled", "true")
        await set_platform_setting(sm, "real_name_required_for_recharge", "true")
        set_kyc_provider(_Provider(True))
        headers, _ = await user_headers_with_id(client, "13800000169")
        resp = await client.post("/api/v1/me/kyc", json=_body(), headers=headers)
        assert resp.status_code == 409
        assert resp.json()["message_key"] == "account.kycNotAvailable"
        recharge = await client.post(
            "/api/v1/wallet/recharges", json={"amount": "50.00", "channel": "mock"}, headers=headers
        )
        assert recharge.status_code == 201, recharge.text

    async def test_enabled_without_credentials_is_502_not_500(self, client, sm):
        await set_platform_setting(sm, "real_name_enabled", "true")
        headers, _ = await _phone_user(client, sm, "13800000164")
        resp = await client.post("/api/v1/me/kyc", json=_body(), headers=headers)
        assert resp.status_code == 502
        assert resp.json()["code"] == "REAL_NAME_CHANNEL_ERROR"

    async def test_aliyun_provider_needs_plus86_phone(self, client, sm, monkeypatch):
        """Email-only account with the Aliyun provider → 400 kycRegionUnsupported, no API call."""
        await set_platform_setting(sm, "real_name_enabled", "true")
        set_kyc_provider(AliyunMobile3Provider("ak", "sk"))
        monkeypatch.setattr(get_settings(), "compliance_profile", None)
        headers, _ = await user_headers_with_id(client, "13800000167")
        monkeypatch.setattr(get_settings(), "compliance_profile", "cn")
        resp = await client.post("/api/v1/me/kyc", json=_body(), headers=headers)
        assert resp.status_code == 400
        assert resp.json()["message_key"] == "account.kycRegionUnsupported"


class TestGates:
    async def test_recharge_gate_when_required(self, client, sm):
        settings = get_settings()
        settings.real_name_enabled = True
        settings.real_name_required_for_recharge = True
        set_kyc_provider(_Provider(True))
        try:
            headers, _ = await _phone_user(client, sm, "13800000162")
            resp = await client.post(
                "/api/v1/wallet/recharges",
                json={"amount": "50.00", "channel": "mock"},
                headers=headers,
            )
            assert resp.status_code == 403
            assert resp.json()["code"] == "REAL_NAME_REQUIRED"
            await client.post(
                "/api/v1/me/kyc",
                json=_body(OTHER_ID, "王五"),  # cjk-ok
                headers=headers,  # cjk-ok
            )  # cjk-ok
            resp = await client.post(
                "/api/v1/wallet/recharges",
                json={"amount": "50.00", "channel": "mock"},
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
        finally:
            settings.real_name_required_for_recharge = False
            settings.real_name_enabled = False

    async def test_create_instance_gate_when_required(self, client, sm):
        settings = get_settings()
        settings.real_name_enabled = True
        settings.real_name_required_for_recharge = True
        set_kyc_provider(_Provider(True))
        try:
            headers, _user_id, key_id = await _funded_phone_user(client, sm, "13800000165")
            sku_id = await create_test_sku(sm)
            await seed_node_spec(sm)
            resp = await client.post(
                "/api/v1/instances",
                json={"sku_id": sku_id, "image_ref": "img", "ssh_key_ids": [key_id]},
                headers=headers,
            )
            assert resp.status_code == 403
            assert resp.json()["message_key"] == "orchestrator.realNameRequired"
            await client.post(
                "/api/v1/me/kyc",
                json=_body("110101199001012221", "赵六"),  # cjk-ok
                headers=headers,  # cjk-ok
            )
            resp = await client.post(
                "/api/v1/instances",
                json={"sku_id": sku_id, "image_ref": "img", "ssh_key_ids": [key_id]},
                headers=headers,
            )
            assert resp.status_code == 202, resp.text
        finally:
            settings.real_name_required_for_recharge = False
            settings.real_name_enabled = False

    async def test_start_and_disk_gates_when_required(self, client, sm):
        settings = get_settings()
        settings.real_name_enabled = True
        settings.real_name_required_for_recharge = True
        set_kyc_provider(_Provider(True))
        try:
            headers, user_id, _key_id = await _funded_phone_user(client, sm, "13800000166")
            await create_test_sku(sm)
            await seed_node_spec(sm)
            resp = await client.post(
                "/api/v1/disks", json={"name": "d1", "size_gb": 10}, headers=headers
            )
            assert resp.status_code == 403
            assert resp.json()["message_key"] == "disks.realNameRequired"
            _, uuid = await seed_instance(sm, user_id=user_id, status="stopped")
            resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
            assert resp.status_code == 403
            await client.post(
                "/api/v1/me/kyc",
                json=_body("110101199001013339", "钱七"),  # cjk-ok
                headers=headers,  # cjk-ok
            )
            resp = await client.post(
                "/api/v1/disks", json={"name": "d1", "size_gb": 10}, headers=headers
            )
            assert resp.status_code == 201, resp.text
            resp = await client.post(f"/api/v1/instances/{uuid}/start", headers=headers)
            assert resp.status_code == 200, resp.text
        finally:
            settings.real_name_required_for_recharge = False
            settings.real_name_enabled = False

    async def test_register_requires_terms(self, client):
        await client.post(
            "/api/v1/auth/verification-code",
            json={"handle": as_handle("13800000163"), "purpose": "register"},
        )
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": as_handle("13800000163"), "email_code": "123456"},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "TERMS_NOT_ACCEPTED"
