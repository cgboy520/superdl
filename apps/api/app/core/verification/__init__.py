"""Verification-code delivery copy shared by the SMS and email channels."""

from app.core.verification.templates import EmailContent, code_email, sms_text

__all__ = ["EmailContent", "code_email", "sms_text"]
