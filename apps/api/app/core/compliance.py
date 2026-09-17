"""Compliance profiles: what a deployment's jurisdiction requires of signup, KYC and invoicing.

The active profile is deployment identity (`SUPERDL_COMPLIANCE_PROFILE`), not an online setting.
`none` is the international default; `cn` turns on the mainland-China rules.
"""

from dataclasses import dataclass
from typing import Literal

from app.core.config import get_settings

ProfileName = Literal["none", "cn"]


@dataclass(frozen=True)
class ComplianceProfile:
    """`phone_dial_codes` empty = any region; `kyc_form` None = no identity-verification form;
    `invoice_tax_id_rule` names the tax-ID validator billing applies to invoice requests."""

    name: ProfileName
    phone_required: bool
    phone_dial_codes: tuple[str, ...]
    kyc_form: Literal["cn_id_card"] | None
    invoice_tax_id_rule: Literal["cn_uscc"] | None
    default_locale: str


PROFILES: dict[ProfileName, ComplianceProfile] = {
    "none": ComplianceProfile(
        name="none",
        phone_required=False,
        phone_dial_codes=(),
        kyc_form=None,
        invoice_tax_id_rule=None,
        default_locale="en-US",
    ),
    "cn": ComplianceProfile(
        name="cn",
        phone_required=True,
        phone_dial_codes=("86",),
        kyc_form="cn_id_card",
        invoice_tax_id_rule="cn_uscc",
        default_locale="zh-CN",
    ),
}


def profile_for(name: str | None) -> ComplianceProfile:
    """Unset (dev/test without an explicit profile) resolves to `none`."""
    return PROFILES[name or "none"]  # type: ignore[index]


def current_profile() -> ComplianceProfile:
    return profile_for(get_settings().compliance_profile)
