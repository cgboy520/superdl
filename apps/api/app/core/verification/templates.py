"""Verification-code copy for SMS bodies and emails, per locale (en-US, zh-CN).

Not part of the error-message catalog: these strings leave the platform through a carrier, so
they are rendered server-side from the caller's negotiated locale.
"""

import html
from dataclasses import dataclass

from app.core.locale import DEFAULT_LOCALE, Locale

_SMS: dict[str, dict[str, str]] = {
    "en-US": {
        "verify": "[SuperDL] Your verification code is {code}. Do not share it with anyone.",
        "notice": "[SuperDL] {title}",
    },
    "zh-CN": {
        "verify": "【SuperDL】您的验证码是 {code},请勿泄露给他人。",
        "notice": "【SuperDL】{title}",
    },
}

_EMAIL_SUBJECT: dict[str, dict[str, str]] = {
    "en-US": {
        "register": "Confirm your SuperDL sign-up",
        "login": "Your SuperDL sign-in code",
        "reset_password": "Reset your SuperDL password",
        "bind_handle": "Confirm your new SuperDL contact",
        "test": "SuperDL email channel test",
    },
    "zh-CN": {
        "register": "确认注册 SuperDL",
        "login": "您的 SuperDL 登录验证码",
        "reset_password": "重置 SuperDL 密码",
        "bind_handle": "确认新的 SuperDL 联系方式",
        "test": "SuperDL 邮件渠道测试",
    },
}

_EMAIL_BODY: dict[str, str] = {
    "en-US": (
        "Your verification code is {code}.\n\n"
        "It can be used once and expires shortly. If you did not request it, ignore this email."
    ),
    "zh-CN": "您的验证码是 {code}。\n\n验证码仅可使用一次并很快过期;如非本人操作,请忽略本邮件。",
}


@dataclass(frozen=True)
class EmailContent:
    subject: str
    text: str
    html: str


def _pick(table: dict[str, dict[str, str]], locale: str) -> dict[str, str]:
    return table.get(locale, table[DEFAULT_LOCALE])


def sms_text(kind: str, params: dict[str, str], locale: Locale = DEFAULT_LOCALE) -> str:
    """Body for a `verify` (params: code) or `notice` (params: title) SMS."""
    return _pick(_SMS, locale)[kind].format_map(params)


def code_email(purpose: str, code: str, locale: Locale = DEFAULT_LOCALE) -> EmailContent:
    """Subject, text and HTML for a verification-code email; unknown purposes use the login copy."""
    subjects = _pick(_EMAIL_SUBJECT, locale)
    subject = subjects.get(purpose, subjects["login"])
    text = _EMAIL_BODY.get(locale, _EMAIL_BODY[DEFAULT_LOCALE]).format(code=code)
    paragraphs = "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n"))
    return EmailContent(
        subject=subject,
        text=text,
        html=paragraphs.replace(html.escape(code), f"<strong>{html.escape(code)}</strong>", 1),
    )
