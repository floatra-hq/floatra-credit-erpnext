# -*- coding: utf-8 -*-
"""PII redaction for the Floatra Webhook Log payload column.

ERP Spec §14 + Floatra Platform Credit Policy:
    "Do not log raw Floatra API responses that contain BVN, NIN, or
    bank account data."

The webhook log persists every inbound event body verbatim for ops
debugging. Some events (notably ``merchant.kyc_completed``) carry
PII the spec explicitly prohibits us from storing in plaintext on
the ERP side. We walk the payload before persistence and replace the
values of known-sensitive keys with ``[REDACTED]`` — keys themselves
are preserved so ops can still see WHICH PII was present, just not
WHAT.

This is conservative: we redact a wide superset of keys including
contact information (phone, email) and any field whose name suggests
biometric / financial data. Adding a key to the allowlist is much
safer than missing one.
"""

from typing import Any

# Lowercased key names whose values must never reach the log.
# Includes both the canonical spec names and reasonable variants
# (camelCase, snake_case, with/without _hash suffix).
_REDACT_KEYS: frozenset[str] = frozenset({
    # Identity numbers
    "bvn",
    "nin",
    "bvn_hash",
    "nin_hash",
    "bvnhash",
    "ninhash",
    # Bank account
    "account_number",
    "accountnumber",
    "account_no",
    "bank_account",
    "bank_account_number",
    # Contact PII
    "phone",
    "phone_number",
    "phonenumber",
    "msisdn",
    "email",
    "email_address",
    # Biometrics
    "selfie",
    "selfie_url",
    "selfie_image",
    "photo",
    "photo_url",
    "face_image",
    # Government ID images / document content
    "id_image",
    "id_document",
    "document_image",
    "document_url",
    # Hosted KYC link (a bearer link, OTP-gated) — never in a log.
    "kyc_url",
})

_REDACTED = "[REDACTED]"


def redact_pii(payload: Any) -> Any:
    """Return a copy of ``payload`` with the values of any
    PII-bearing keys replaced by ``[REDACTED]``.

    Walks dicts recursively and lists element-wise. Non-container
    values are returned unchanged. Keys themselves are preserved
    so the log row still shows which sensitive fields were present.
    """
    if isinstance(payload, dict):
        return {
            key: _REDACTED if key.lower() in _REDACT_KEYS
            else redact_pii(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_pii(item) for item in payload]
    return payload
