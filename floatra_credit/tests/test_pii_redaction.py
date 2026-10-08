# -*- coding: utf-8 -*-
"""Pure-Python tests for the webhook-log PII redactor.

ERP Spec §14 and Floatra's credit policy prohibit storing BVN, NIN,
bank-account, contact and biometric fields in plaintext on the ERP
side. The redactor walks an inbound webhook payload and replaces
the values of known-sensitive keys with ``[REDACTED]`` before the
JSON dump that lands in the ``Floatra Webhook Log`` row.
"""

import unittest

from ..services.pii_redaction import redact_pii


class TestPiiRedaction(unittest.TestCase):
    def test_redacts_bvn_and_nin(self):
        out = redact_pii({
            "event": "merchant.kyc_completed",
            "data": {
                "bvn": "12345678901",
                "nin": "98765432109",
                "external_merchant_id": "ERP-CUST-001",
            },
        })
        self.assertEqual(out["data"]["bvn"], "[REDACTED]")
        self.assertEqual(out["data"]["nin"], "[REDACTED]")
        # Non-PII fields untouched.
        self.assertEqual(out["data"]["external_merchant_id"], "ERP-CUST-001")
        self.assertEqual(out["event"], "merchant.kyc_completed")

    def test_redacts_the_hosted_kyc_link(self):
        out = redact_pii({"data": {"kyc_url": "https://floatra.com/kyc/t", "expires_at": "x"}})
        self.assertEqual(out["data"]["kyc_url"], "[REDACTED]")
        self.assertEqual(out["data"]["expires_at"], "x")

    def test_redacts_bvn_and_nin_hash_variants(self):
        # The audit explicitly called out that hashes (already
        # one-way) still shouldn't be log-persisted: even a hash
        # tells an attacker which values are present.
        out = redact_pii({
            "data": {
                "bvn_hash": "abc...",
                "nin_hash": "def...",
            },
        })
        self.assertEqual(out["data"]["bvn_hash"], "[REDACTED]")
        self.assertEqual(out["data"]["nin_hash"], "[REDACTED]")

    def test_redacts_bank_account_fields(self):
        out = redact_pii({
            "data": {
                "bank_account": {
                    "bank_code": "058",
                    "account_number": "0123456789",
                },
            },
        })
        # Both the parent value and the nested account_number are
        # redacted. bank_account is in the key allowlist so the whole
        # value goes to [REDACTED]; account_number on its own is also
        # in the allowlist for the nested-only case.
        self.assertEqual(out["data"]["bank_account"], "[REDACTED]")

    def test_redacts_phone_and_email(self):
        out = redact_pii({
            "data": {
                "phone": "+234801234567",
                "email": "owner@distributor.com",
            },
        })
        self.assertEqual(out["data"]["phone"], "[REDACTED]")
        self.assertEqual(out["data"]["email"], "[REDACTED]")

    def test_redacts_biometric_urls(self):
        out = redact_pii({
            "data": {
                "selfie_url": "https://cdn.smileid.com/sel.jpg",
                "id_image": "https://cdn.smileid.com/id.jpg",
            },
        })
        self.assertEqual(out["data"]["selfie_url"], "[REDACTED]")
        self.assertEqual(out["data"]["id_image"], "[REDACTED]")

    def test_preserves_keys(self):
        # Ops needs to see WHICH PII fields were present, just not
        # WHAT — keep the key, redact the value.
        out = redact_pii({"bvn": "12345678901"})
        self.assertIn("bvn", out)
        self.assertNotIn("12345678901", str(out))

    def test_walks_lists(self):
        out = redact_pii({
            "data": {
                "owners": [
                    {"name": "Alice", "bvn": "11111111111"},
                    {"name": "Bob",   "bvn": "22222222222"},
                ],
            },
        })
        self.assertEqual(out["data"]["owners"][0]["bvn"], "[REDACTED]")
        self.assertEqual(out["data"]["owners"][1]["bvn"], "[REDACTED]")
        # Names are not PII per spec — left alone.
        self.assertEqual(out["data"]["owners"][0]["name"], "Alice")

    def test_walks_deeply_nested_payload(self):
        out = redact_pii({
            "outer": {
                "middle": {
                    "inner": {
                        "bvn": "12345678901",
                        "harmless": "stays",
                    },
                },
            },
        })
        self.assertEqual(
            out["outer"]["middle"]["inner"]["bvn"],
            "[REDACTED]",
        )
        self.assertEqual(
            out["outer"]["middle"]["inner"]["harmless"],
            "stays",
        )

    def test_does_not_mutate_input(self):
        original = {"bvn": "12345678901", "name": "Acme Distributor"}
        out = redact_pii(original)
        # The input must remain pristine so handlers that run after
        # the log row is written still see the real values.
        self.assertEqual(original["bvn"], "12345678901")
        self.assertEqual(out["bvn"], "[REDACTED]")

    def test_returns_non_container_inputs_unchanged(self):
        self.assertEqual(redact_pii("not-a-dict"), "not-a-dict")
        self.assertEqual(redact_pii(42), 42)
        self.assertIsNone(redact_pii(None))

    def test_camelcase_variants(self):
        # The redactor matches keys case-insensitively. Spec uses
        # snake_case but partner payloads sometimes leak camelCase.
        out = redact_pii({
            "phoneNumber": "+234801",
            "accountNumber": "0123456789",
        })
        self.assertEqual(out["phoneNumber"], "[REDACTED]")
        self.assertEqual(out["accountNumber"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
