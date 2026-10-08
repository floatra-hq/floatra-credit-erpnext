# -*- coding: utf-8 -*-
"""Frappe-runtime test: the Sales Order before_submit gate.

Verifies the structural credit guard from `controllers/sales_order.py`:
a Sales Order whose Floatra credit is pending / approved / waitlisted
but not yet funded (`floatra_disbursed = 0`) MUST refuse to submit. Without this gate, a sales user could ship
goods Floatra hasn't funded.

The test creates a real Sales Order via the Frappe ORM and exercises
the submit lifecycle.
"""

import frappe
import unittest


CUSTOMER_NAME = "Floatra Credit Test Customer"
ITEM_CODE = "Floatra Credit Test Item"
CUSTOMER_GROUP = "Floatra Test Customer Group"
TERRITORY = "Floatra Test Territory"


def _ensure_customer_group():
    if not frappe.db.exists("Customer Group", CUSTOMER_GROUP):
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": CUSTOMER_GROUP,
            "parent_customer_group": "All Customer Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)


def _ensure_territory():
    if not frappe.db.exists("Territory", TERRITORY):
        frappe.get_doc({
            "doctype": "Territory",
            "territory_name": TERRITORY,
            "parent_territory": "All Territories",
            "is_group": 0,
        }).insert(ignore_permissions=True)


def _ensure_customer():
    _ensure_customer_group()
    _ensure_territory()
    if not frappe.db.exists("Customer", CUSTOMER_NAME):
        frappe.get_doc({
            "doctype": "Customer",
            "customer_name": CUSTOMER_NAME,
            "customer_group": CUSTOMER_GROUP,
            "territory": TERRITORY,
            "floatra_merchant_id": "MERCH-TEST-001",
            "floatra_credit_status": "available",
        }).insert(ignore_permissions=True)


ITEM_GROUP = "Floatra Test Item Group"


def _ensure_item_group():
    if not frappe.db.exists("Item Group", ITEM_GROUP):
        frappe.get_doc({
            "doctype": "Item Group",
            "item_group_name": ITEM_GROUP,
            "parent_item_group": "All Item Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)


def _ensure_item():
    _ensure_item_group()
    if not frappe.db.exists("Item", ITEM_CODE):
        frappe.get_doc({
            "doctype": "Item",
            "item_code": ITEM_CODE,
            "item_name": ITEM_CODE,
            "item_group": ITEM_GROUP,
            "is_stock_item": 0,
            "stock_uom": "Nos",
        }).insert(ignore_permissions=True)


def _make_sales_order(floatra_fields: dict) -> "frappe.Document":
    _ensure_customer()
    _ensure_item()
    so = frappe.get_doc({
        "doctype": "Sales Order",
        "customer": CUSTOMER_NAME,
        "delivery_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
        "items": [
            {
                "item_code": ITEM_CODE,
                "qty": 1,
                "rate": 50000,
                "delivery_date": frappe.utils.add_days(
                    frappe.utils.nowdate(), 7,
                ),
            }
        ],
        **floatra_fields,
    })
    so.insert(ignore_permissions=True)
    return so


class TestSalesOrderBeforeSubmitGate(unittest.TestCase):
    def test_blocks_submit_when_credit_pending(self):
        # Credit requested but not yet disbursed.
        so = _make_sales_order({
            "floatra_order_id": "FLT-ORDER-TEST-1",
            "floatra_decision": "approved",
            "floatra_disbursed": 0,
        })
        with self.assertRaises(frappe.ValidationError):
            so.submit()

    def test_allows_submit_when_disbursement_confirmed(self):
        so = _make_sales_order({
            "floatra_order_id": "FLT-ORDER-TEST-2",
            "floatra_decision": "approved",
            "floatra_disbursed": 1,
        })
        # Should not raise.
        so.submit()
        self.assertEqual(so.docstatus, 1)

    def test_blocks_submit_while_waitlisted(self):
        so = _make_sales_order({
            "floatra_order_id": "FLT-ORDER-TEST-4",
            "floatra_decision": "waitlisted",
        })
        with self.assertRaises(frappe.ValidationError):
            so.submit()

    def test_allows_submit_when_no_floatra_credit_attached(self):
        # Standard non-Floatra Sales Order — no gate fires.
        so = _make_sales_order({})
        so.submit()
        self.assertEqual(so.docstatus, 1)

    def test_allows_submit_when_decision_declined(self):
        # A declined order carries Floatra's order id (initiate returns
        # one for every decision) but no credit: it ships on the
        # distributor's own terms. The gate keys on the decision.
        so = _make_sales_order({
            "floatra_order_id": "FLT-ORDER-TEST-3",
            "floatra_decision": "declined",
        })
        so.submit()
        self.assertEqual(so.docstatus, 1)
