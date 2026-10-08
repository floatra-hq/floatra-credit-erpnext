# -*- coding: utf-8 -*-
"""P2 batch 12: orders reconciliation (P2-13 slice).

Two surfaces under test:

  1. `FloatraAPIClient.list_orders` — GET /v1/partner/orders query
     assembly.

  2. `api.action_reconcile_orders` — pages the gateway's order
     list, buckets each row into synced / missing_local /
     status_drift against ERPNext Sales Orders.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from ...services.floatra_api import FloatraAPIClient
from ...services.floatra_contract import parse_orders_page


CUSTOMER_GROUP_FIXTURE = "Floatra P2-12 Test Group"
TERRITORY_FIXTURE = "Floatra P2-12 Test Territory"
ITEM_GROUP_FIXTURE = "Floatra P2-12 Test Items"
ITEM_CODE_FIXTURE = "FLT-P2B12-SKU"


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


class TestListOrdersClient(unittest.TestCase):
    """Wire-shape tests for `FloatraAPIClient.list_orders`."""

    def setUp(self):
        config = frappe.get_single("Floatra Platform Config")
        config.floatra_gateway_url = "https://api.floatra.test"
        config.platform_id = "test-platform"
        config.api_key = "test-api-key-do-not-use"
        config.webhook_secret = "test-secret-do-not-use"
        config.save(ignore_permissions=True)

    def _query(self, **kwargs):
        client = FloatraAPIClient.for_site()
        with patch.object(client, "_request", return_value={
            "data": [], "next_cursor": None, "has_more": False,
        }) as patched:
            client.list_orders(**kwargs)
        method, segments = patched.call_args[0]
        self.assertEqual((method, segments), ("GET", ("orders",)))
        query = patched.call_args[1]["query"]
        return {k: v for k, v in query.items() if v is not None}

    def test_omits_query_string_when_no_filters(self):
        self.assertEqual(self._query(), {})

    def test_builds_query_string_with_status_filter(self):
        self.assertEqual(
            self._query(status="ACTIVE", limit=25), {"status": "ACTIVE", "limit": 25},
        )

    def test_preserves_cursor_param_for_pagination(self):
        self.assertEqual(
            self._query(cursor="opaque-cursor-token"),
            {"cursor": "opaque-cursor-token"},
        )


class TestActionReconcileOrders(unittest.TestCase):
    """`api.action_reconcile_orders` — synced / missing_local /
    status_drift bucketing.
    """

    def setUp(self):
        _ensure_dependencies()
        config = frappe.get_single("Floatra Platform Config")
        config.floatra_gateway_url = "https://api.floatra.test"
        config.platform_id = "test-platform"
        config.api_key = "test-api-key-do-not-use"
        config.webhook_secret = "test-secret-do-not-use"
        config.save(ignore_permissions=True)

        # Mint a Customer + SO that the reconciliation will join
        # against. Floatra's rows are loans (their `floatra_order_id` is the
        # loan id); the Sales Orders carry it as `floatra_loan_id`.
        self.customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": "P2-12 Recon Customer "
                + frappe.generate_hash(length=6),
            "customer_type": "Company",
            "customer_group": CUSTOMER_GROUP_FIXTURE,
            "territory": TERRITORY_FIXTURE,
            "floatra_merchant_id": "FLT-M-RECON-001",
        }).insert(ignore_permissions=True)

        from frappe.utils import add_days, nowdate
        self.so_synced = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": self.customer.name,
            "delivery_date": add_days(nowdate(), 7),
            "items": [{
                "item_code": ITEM_CODE_FIXTURE,
                "qty": 1,
                "rate": 50000,
                "delivery_date": add_days(nowdate(), 7),
            }],
            "floatra_loan_id": "FLT-ORD-SYNCED",
            "floatra_decision": "approved",
            "floatra_disbursed": 1,
            "floatra_delivery_confirmed": 1,
        }).insert(ignore_permissions=True)

        # Drift case: local SO exists but `floatra_disbursed = 0`
        # while Floatra says ACTIVE.
        self.so_drift = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": self.customer.name,
            "delivery_date": add_days(nowdate(), 7),
            "items": [{
                "item_code": ITEM_CODE_FIXTURE,
                "qty": 1,
                "rate": 50000,
                "delivery_date": add_days(nowdate(), 7),
            }],
            "floatra_loan_id": "FLT-ORD-DRIFT",
            "floatra_decision": "approved",
            "floatra_disbursed": 0,
            "floatra_delivery_confirmed": 0,
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    def tearDown(self):
        try:
            for so in (self.so_drift, self.so_synced):
                frappe.delete_doc(
                    "Sales Order", so.name, force=True,
                    ignore_permissions=True,
                )
            frappe.delete_doc(
                "Customer", self.customer.name, force=True,
                ignore_permissions=True,
            )
            frappe.db.commit()
        except Exception:
            pass

    def _patch_client(self, envelope):
        fake = MagicMock(spec=FloatraAPIClient)
        fake.list_orders.return_value = parse_orders_page(envelope)
        return patch.object(
            FloatraAPIClient, "for_site", return_value=fake,
        )

    def test_buckets_synced_missing_and_drift(self):
        from ... import api
        envelope = {
            "data": [
                {
                    "floatra_order_id": "FLT-ORD-SYNCED",
                    "floatra_merchant_id": "FLT-M-RECON-001",
                    "status": "ACTIVE",
                    "created_at": "2026-06-01T08:00:00Z",
                },
                {
                    "floatra_order_id": "FLT-ORD-DRIFT",
                    "floatra_merchant_id": "FLT-M-RECON-001",
                    "status": "ACTIVE",
                    "created_at": "2026-06-01T08:00:00Z",
                },
                {
                    "floatra_order_id": "FLT-ORD-MISSING",
                    "floatra_merchant_id": "FLT-M-RECON-001",
                    "status": "ACTIVE",
                    "created_at": "2026-06-01T08:00:00Z",
                },
            ],
            "next_cursor": None,
            "has_more": False,
        }
        with self._patch_client(envelope):
            result = api.action_reconcile_orders()
        self.assertEqual(result["scanned"], 3)
        self.assertEqual(result["synced"], 1)
        self.assertEqual(len(result["missing_local"]), 1)
        self.assertEqual(
            result["missing_local"][0]["floatra_loan_id"],
            "FLT-ORD-MISSING",
        )
        self.assertEqual(len(result["status_drift"]), 1)
        self.assertEqual(
            result["status_drift"][0]["floatra_loan_id"],
            "FLT-ORD-DRIFT",
        )

    def test_refuses_unknown_status_filter(self):
        from ... import api
        with self.assertRaises(frappe.ValidationError):
            api.action_reconcile_orders(status="ENTROPY")

    def test_walks_pagination_via_cursor(self):
        from ... import api

        page_1 = {
            "data": [{
                "floatra_order_id": "FLT-ORD-SYNCED",
                "status": "ACTIVE",
                "floatra_merchant_id": "FLT-M-RECON-001",
                "created_at": "2026-06-01T08:00:00Z",
            }],
            "next_cursor": "page-2-token",
            "has_more": True,
        }
        page_2 = {
            "data": [{
                "floatra_order_id": "FLT-ORD-DRIFT",
                "status": "ACTIVE",
                "floatra_merchant_id": "FLT-M-RECON-001",
                "created_at": "2026-06-01T08:00:00Z",
            }],
            "next_cursor": None,
            "has_more": False,
        }
        pages = [page_1, page_2]

        fake = MagicMock(spec=FloatraAPIClient)
        fake.list_orders.side_effect = lambda **_kw: parse_orders_page(
            pages.pop(0) if pages else
            {"data": [], "next_cursor": None, "has_more": False}
        )
        with patch.object(
            FloatraAPIClient, "for_site", return_value=fake,
        ):
            result = api.action_reconcile_orders()

        self.assertEqual(fake.list_orders.call_count, 2)
        # First call used cursor=None; second carried the page_1 cursor.
        second_call_kwargs = fake.list_orders.call_args_list[1].kwargs
        self.assertEqual(second_call_kwargs.get("cursor"), "page-2-token")
        self.assertEqual(result["scanned"], 2)

    def test_caps_max_orders(self):
        from ... import api
        # Build a single page of 5 rows; ask for max_orders=2.
        rows = [
            {
                "floatra_order_id": f"FLT-ORD-CAP-{i}",
                "status": "ACTIVE",
                "floatra_merchant_id": "FLT-M-RECON-001",
                "created_at": "2026-06-01T08:00:00Z",
            }
            for i in range(5)
        ]
        envelope = {
            "data": rows, "next_cursor": None, "has_more": False,
        }
        with self._patch_client(envelope):
            result = api.action_reconcile_orders(max_orders=2)
        self.assertEqual(result["scanned"], 2)
