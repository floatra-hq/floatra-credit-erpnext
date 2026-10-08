# -*- coding: utf-8 -*-
"""P2 batch 10: Return + key-rotation-complete client surfaces.

Two surfaces under test:

  1. `api.action_initiate_return` — whitelisted endpoint that posts
     a return to /v1/partner/orders/:loan_id/return. Guards on funded;
     rejects invalid reason codes and non-positive amounts.

  2. `services.floatra_api.FloatraAPIClient.post_key_rotation_complete`
     — thin POST wrapper. We exercise the wire shape against a
     patched `_request` so we don't need a real gateway.

Customer / Sales Order setup mirrors `test_p2_batch_9.py` — the
default "All Customer Groups" / "All Territories" are themselves
group entries and refuse selection, so we mint non-group fixtures.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from ...services.floatra_api import FloatraAPIClient, FloatraAPIError


CUSTOMER_GROUP_FIXTURE = "Floatra P2-10 Test Group"
TERRITORY_FIXTURE = "Floatra P2-10 Test Territory"
ITEM_GROUP_FIXTURE = "Floatra P2-10 Test Items"
ITEM_CODE_FIXTURE = "FLT-P2B10-SKU"


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
    if not frappe.db.exists("Item Group", ITEM_GROUP_FIXTURE):
        frappe.get_doc({
            "doctype": "Item Group",
            "item_group_name": ITEM_GROUP_FIXTURE,
            "parent_item_group": "All Item Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Item", ITEM_CODE_FIXTURE):
        frappe.get_doc({
            "doctype": "Item",
            "item_code": ITEM_CODE_FIXTURE,
            "item_name": ITEM_CODE_FIXTURE,
            "item_group": ITEM_GROUP_FIXTURE,
            "is_stock_item": 0,
            "stock_uom": "Nos",
        }).insert(ignore_permissions=True)


class TestActionInitiateReturn(unittest.TestCase):
    """The whitelisted Return endpoint (P2-13 batch 10)."""

    def setUp(self):
        _ensure_dependencies()
        customer_name = (
            "P2-batch10 Customer " + frappe.generate_hash(length=6)
        )
        self.customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Company",
            "customer_group": CUSTOMER_GROUP_FIXTURE,
            "territory": TERRITORY_FIXTURE,
            "floatra_merchant_id": "FLT-M-RTN-001",
            "floatra_credit_status": "available",
        }).insert(ignore_permissions=True)

        from frappe.utils import add_days, nowdate

        self.so = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": self.customer.name,
            "delivery_date": add_days(nowdate(), 7),
            "items": [
                {
                    "item_code": ITEM_CODE_FIXTURE,
                    "qty": 1,
                    "rate": 50000,
                    "delivery_date": add_days(nowdate(), 7),
                },
            ],
            "floatra_order_id": "FLT-ORD-RTN-001",
            "floatra_loan_id": "loan_01",
            "floatra_decision": "approved",
            "floatra_disbursed": 1,
            "floatra_delivery_confirmed": 1,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        try:
            frappe.delete_doc(
                "Sales Order", self.so.name, force=True,
                ignore_permissions=True,
            )
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

    def test_refuses_so_not_yet_disbursed(self):
        from ... import api
        self.so.db_set("floatra_disbursed", 0)
        with self.assertRaises(frappe.ValidationError):
            api.action_initiate_return(
                self.so.name, 5000, "DAMAGED",
            )

    def test_refuses_non_positive_amount(self):
        from ... import api
        with self.assertRaises(frappe.ValidationError):
            api.action_initiate_return(
                self.so.name, 0, "DAMAGED",
            )
        with self.assertRaises(frappe.ValidationError):
            api.action_initiate_return(
                self.so.name, -1, "DAMAGED",
            )

    def test_refuses_unknown_reason_code(self):
        from ... import api
        with self.assertRaises(frappe.ValidationError):
            api.action_initiate_return(
                self.so.name, 5000, "ENTROPY",
            )

    def test_happy_path_calls_api_with_idempotent_key(self):
        from ... import api
        with self._patch_client(
            initiate_return={"status": "INITIATED"},
        ) as patched:
            envelope = api.action_initiate_return(
                self.so.name,
                return_amount="50000",
                reason_code="DAMAGED",
                reason_notes="Two cartons crushed in transit",
            )
            client = patched.return_value
            client.initiate_return.assert_called_once()
            args, kwargs = client.initiate_return.call_args
            self.assertEqual(args[0], "loan_01")  # the LOAN id
            payload = args[1]
            self.assertEqual(payload["return_amount"], "50000.00")
            self.assertNotIn("return_amount_kobo", payload)
            self.assertNotIn("idempotency_key", payload)
            self.assertEqual(payload["reason_code"], "DAMAGED")
            self.assertEqual(kwargs["idempotency_key"], f"so-return:{self.so.name}")
            self.assertIn("crushed", payload["reason_notes"])
        self.assertEqual(envelope, {"status": "INITIATED"})

    def test_surfaces_floatra_rejection_as_validation_error(self):
        from ... import api
        fake = MagicMock(spec=FloatraAPIClient)
        fake.initiate_return.side_effect = FloatraAPIError(
            422, {"success": False, "errorCode": "RETURN_015"},
        )
        with patch.object(
            FloatraAPIClient, "for_site", return_value=fake,
        ):
            with self.assertRaises(frappe.ValidationError):
                api.action_initiate_return(
                    self.so.name, 5000, "DAMAGED",
                )


class TestPostKeyRotationComplete(unittest.TestCase):
    """The `FloatraAPIClient.post_key_rotation_complete` client
    method. No UI — we exercise the wire shape directly.
    """

    def setUp(self):
        config = frappe.get_single("Floatra Platform Config")
        config.floatra_gateway_url = "https://api.floatra.test"
        config.platform_id = "test-platform"
        config.api_key = "test-api-key-do-not-use"
        config.webhook_secret = "test-secret-do-not-use"
        config.save(ignore_permissions=True)

    def test_posts_to_platform_key_rotation_complete_with_empty_body(self):
        client = FloatraAPIClient.for_site()
        with patch.object(
            client, "_request",
            return_value={
                "platform_id": "test-platform",
                "already_retired": False,
                "retired_at": "2026-06-01T10:00:00Z",
            },
        ) as patched:
            envelope = client.post_key_rotation_complete()
            patched.assert_called_once_with(
                "POST", ("platform", "key-rotation-complete"), {},
            )
        self.assertEqual(envelope["platform_id"], "test-platform")
        self.assertFalse(envelope["already_retired"])

    def test_handles_already_retired_idempotent_response(self):
        client = FloatraAPIClient.for_site()
        with patch.object(
            client, "_request",
            return_value={
                "platform_id": "test-platform",
                "already_retired": True,
                "retired_at": None,
            },
        ):
            envelope = client.post_key_rotation_complete()
            self.assertTrue(envelope["already_retired"])
