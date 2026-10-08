# -*- coding: utf-8 -*-
"""P2 batch 11: bank-account + bulk-orders client surfaces.

Two surfaces under test:

  1. `api.action_set_bank_account` — whitelisted endpoint that
     posts a bank-account upsert to /v1/partner/merchants/:id/bank-account.
     Guards on enrolled, 3-10-digit bank_code, exactly-10-digit
     account_number, non-empty bounded account_name.

  2. `services.floatra_api.FloatraAPIClient.bulk_initiate_orders` —
     POST /v1/partner/orders/bulk. Caps at 50 orders client-side; empty
     list rejected. We exercise the wire shape against a patched
     `_request`.

Setup mirrors `test_p2_batch_9.py` / `test_p2_batch_10.py` — the
default "All Customer Groups" / "All Territories" are themselves
group entries and refuse selection.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from ...services.floatra_api import FloatraAPIClient, FloatraAPIError


CUSTOMER_GROUP_FIXTURE = "Floatra P2-11 Test Group"
TERRITORY_FIXTURE = "Floatra P2-11 Test Territory"


def _ensure_dependencies():
    if not frappe.db.exists("Customer Group", CUSTOMER_GROUP_FIXTURE):
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": CUSTOMER_GROUP_FIXTURE,
            "parent_customer_group": "All Customer Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Territory", TERRITORY_FIXTURE):
        frappe.get_doc({
            "doctype": "Territory",
            "territory_name": TERRITORY_FIXTURE,
            "parent_territory": "All Territories",
            "is_group": 0,
        }).insert(ignore_permissions=True)


class TestActionSetBankAccount(unittest.TestCase):
    """The whitelisted Bank Account upsert endpoint (P2-13 batch 11)."""

    GOOD_BANK_CODE = "058"
    GOOD_ACCOUNT_NUMBER = "0123456789"
    GOOD_ACCOUNT_NAME = "Test Distributor Ltd"

    def setUp(self):
        _ensure_dependencies()
        customer_name = (
            "P2-batch11 Customer " + frappe.generate_hash(length=6)
        )
        self.customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Company",
            "customer_group": CUSTOMER_GROUP_FIXTURE,
            "territory": TERRITORY_FIXTURE,
            "floatra_merchant_id": "FLT-M-BANK-001",
            "floatra_credit_status": "available",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        try:
            frappe.delete_doc(
                "Customer", self.customer.name, force=True,
                ignore_permissions=True,
            )
            frappe.db.commit()
        except Exception:
            pass

    def _patch_client(self, **method_returns):
        fake = MagicMock(spec=FloatraAPIClient)
        for method, value in method_returns.items():
            getattr(fake, method).return_value = value
        return patch.object(
            FloatraAPIClient, "for_site", return_value=fake,
        )

    def test_refuses_unenrolled_customer(self):
        from ... import api
        self.customer.db_set("floatra_merchant_id", "")
        with self.assertRaises(frappe.ValidationError):
            api.action_set_bank_account(
                self.customer.name,
                self.GOOD_BANK_CODE,
                self.GOOD_ACCOUNT_NUMBER,
                self.GOOD_ACCOUNT_NAME,
            )

    def test_refuses_bank_code_outside_3_to_10_digit_range(self):
        from ... import api
        # 2 digits — too short
        with self.assertRaises(frappe.ValidationError):
            api.action_set_bank_account(
                self.customer.name, "05",
                self.GOOD_ACCOUNT_NUMBER, self.GOOD_ACCOUNT_NAME,
            )
        # 11 digits — too long
        with self.assertRaises(frappe.ValidationError):
            api.action_set_bank_account(
                self.customer.name, "12345678901",
                self.GOOD_ACCOUNT_NUMBER, self.GOOD_ACCOUNT_NAME,
            )
        # non-numeric
        with self.assertRaises(frappe.ValidationError):
            api.action_set_bank_account(
                self.customer.name, "GTB",
                self.GOOD_ACCOUNT_NUMBER, self.GOOD_ACCOUNT_NAME,
            )

    def test_refuses_account_number_not_exactly_10_digits(self):
        from ... import api
        with self.assertRaises(frappe.ValidationError):
            api.action_set_bank_account(
                self.customer.name,
                self.GOOD_BANK_CODE,
                "123",
                self.GOOD_ACCOUNT_NAME,
            )
        with self.assertRaises(frappe.ValidationError):
            api.action_set_bank_account(
                self.customer.name,
                self.GOOD_BANK_CODE,
                "12345678901",
                self.GOOD_ACCOUNT_NAME,
            )

    def test_refuses_empty_or_overlong_account_name(self):
        from ... import api
        with self.assertRaises(frappe.ValidationError):
            api.action_set_bank_account(
                self.customer.name,
                self.GOOD_BANK_CODE,
                self.GOOD_ACCOUNT_NUMBER,
                "",
            )
        with self.assertRaises(frappe.ValidationError):
            api.action_set_bank_account(
                self.customer.name,
                self.GOOD_BANK_CODE,
                self.GOOD_ACCOUNT_NUMBER,
                "x" * 121,
            )

    def test_happy_path_calls_api_with_idempotency_key(self):
        from ... import api
        with self._patch_client(
            set_bank_account={
                "floatra_merchant_id": "FLT-M-BANK-001",
                "account_number_masked": "********89",
                "is_primary": True,
            },
        ) as patched:
            envelope = api.action_set_bank_account(
                self.customer.name,
                self.GOOD_BANK_CODE,
                self.GOOD_ACCOUNT_NUMBER,
                self.GOOD_ACCOUNT_NAME,
            )
            client = patched.return_value
            client.set_bank_account.assert_called_once()
            args, _ = client.set_bank_account.call_args
            self.assertEqual(args[0], "FLT-M-BANK-001")
            payload = args[1]
            self.assertEqual(payload, {
                "bank_code": "058",
                "account_number": "0123456789",
                "account_name": "Test Distributor Ltd",
                "is_primary": True,
            })
        self.assertIn("account_number_masked", envelope)

    def test_surfaces_floatra_rejection_as_validation_error(self):
        from ... import api
        fake = MagicMock(spec=FloatraAPIClient)
        fake.set_bank_account.side_effect = FloatraAPIError(
            422, {"error": "INVALID_BANK_CODE"},
        )
        with patch.object(
            FloatraAPIClient, "for_site", return_value=fake,
        ):
            with self.assertRaises(frappe.ValidationError):
                api.action_set_bank_account(
                    self.customer.name,
                    self.GOOD_BANK_CODE,
                    self.GOOD_ACCOUNT_NUMBER,
                    self.GOOD_ACCOUNT_NAME,
                )


class TestBulkInitiateOrders(unittest.TestCase):
    """`FloatraAPIClient.bulk_initiate_orders` — wire shape only.

    No UI; the method is a backfill helper. The body comes from
    ``floatra_contract.bulk_body`` (1-50 initiate bodies).
    """

    def setUp(self):
        config = frappe.get_single("Floatra Platform Config")
        config.floatra_gateway_url = "https://api.floatra.test"
        config.platform_id = "test-platform"
        config.api_key = "test-api-key-do-not-use"
        config.webhook_secret = "test-secret-do-not-use"
        config.save(ignore_permissions=True)

    def _one_order(self, n: int = 1) -> dict:
        from ...services.floatra_contract import initiate_body

        return initiate_body(
            external_merchant_id="CUST-BULK-001",
            external_order_id=f"SO-BULK-{n}",
            amount="1000",
            category="FMCG",
            tenure_days=14,
            erp_type="ERPNEXT",
        )

    def test_posts_to_orders_bulk_with_batch_key(self):
        from ...services.floatra_contract import bulk_body

        client = FloatraAPIClient.for_site()
        body = bulk_body(
            [self._one_order(1), self._one_order(2)], "erpnext-2026-06-01-A",
        )
        with patch.object(
            client, "_request",
            return_value={"batch_id": "BATCH-001", "accepted_count": 2,
                          "status": "QUEUED"},
        ) as patched:
            envelope = client.bulk_initiate_orders(body)
            patched.assert_called_once_with(
                "POST", ("orders", "bulk"), body,
                idempotency_key="erpnext-2026-06-01-A",
            )
        self.assertEqual(body["orders"][0]["amount"], "1000.00")
        self.assertEqual(envelope["batch_id"], "BATCH-001")

    def test_rejects_empty_list_locally(self):
        from ...services.floatra_contract import ContractError, bulk_body

        with self.assertRaises(ContractError):
            bulk_body([], "erpnext-empty")

    def test_rejects_more_than_50_orders_locally(self):
        from ...services.floatra_contract import ContractError, bulk_body

        with self.assertRaises(ContractError):
            bulk_body([self._one_order(i) for i in range(51)], "erpnext-oversize")
