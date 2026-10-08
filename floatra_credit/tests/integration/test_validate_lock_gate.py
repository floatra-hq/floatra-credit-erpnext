# -*- coding: utf-8 -*-
"""Frappe-runtime tests for P0-15: the Sales Order `validate` hook.

ERP Spec v3.1 §4 (merchant.reorder_locked event) mandates "ERP MUST:
immediately block ALL new orders for this merchant." v3.0 of the
connector only checked `floatra_disbursed` inside `before_submit` —
so a sales user who never clicked "Request Financing" could
create + submit a Sales Order for a `locked` Customer and ship goods
to a defaulted merchant.

These tests exercise the new `validate` hook against locked,
suspended, and not-locked Customer states.
"""

import frappe
import unittest


CUSTOMER_NAME = "Floatra Validate Lock Test Customer"
ITEM_CODE = "Floatra Validate Lock Test Item"
CUSTOMER_GROUP = "Floatra Test Customer Group"
TERRITORY = "Floatra Test Territory"
ITEM_GROUP = "Floatra Test Item Group"


def _ensure_dependencies():
    if not frappe.db.exists("Customer Group", CUSTOMER_GROUP):
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": CUSTOMER_GROUP,
            "parent_customer_group": "All Customer Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Territory", TERRITORY):
        frappe.get_doc({
            "doctype": "Territory",
            "territory_name": TERRITORY,
            "parent_territory": "All Territories",
            "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Item Group", ITEM_GROUP):
        frappe.get_doc({
            "doctype": "Item Group",
            "item_group_name": ITEM_GROUP,
            "parent_item_group": "All Item Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Item", ITEM_CODE):
        frappe.get_doc({
            "doctype": "Item",
            "item_code": ITEM_CODE,
            "item_name": ITEM_CODE,
            "item_group": ITEM_GROUP,
            "is_stock_item": 0,
            "stock_uom": "Nos",
        }).insert(ignore_permissions=True)


def _ensure_customer(credit_status: str) -> str:
    _ensure_dependencies()
    if frappe.db.exists("Customer", CUSTOMER_NAME):
        frappe.db.set_value(
            "Customer", CUSTOMER_NAME,
            {
                "floatra_credit_status": credit_status,
                "floatra_merchant_id": "MERCH-LOCK-TEST",
            },
        )
    else:
        frappe.get_doc({
            "doctype": "Customer",
            "customer_name": CUSTOMER_NAME,
            "customer_group": CUSTOMER_GROUP,
            "territory": TERRITORY,
            "floatra_merchant_id": "MERCH-LOCK-TEST",
            "floatra_credit_status": credit_status,
        }).insert(ignore_permissions=True)
    return CUSTOMER_NAME


def _build_sales_order_doc(extra: dict | None = None):
    """Build (don't insert yet) so we can assert the validate hook
    fires on insert/save without needing a separate doc-event path.
    """
    return frappe.get_doc({
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
        **(extra or {}),
    })


class TestValidateLockGate(unittest.TestCase):
    """P0-15: customer-status enforcement on save."""

    def test_blocks_save_when_customer_locked(self):
        _ensure_customer("locked")
        so = _build_sales_order_doc()
        with self.assertRaises(frappe.ValidationError) as ctx:
            so.insert(ignore_permissions=True)
        # Spec-compliance: the message must say "locked" so ops sees
        # the right cause in the desk error popup.
        self.assertIn("locked", str(ctx.exception).lower())

    def test_blocks_save_when_customer_suspended(self):
        _ensure_customer("suspended")
        so = _build_sales_order_doc()
        with self.assertRaises(frappe.ValidationError) as ctx:
            so.insert(ignore_permissions=True)
        self.assertIn("suspended", str(ctx.exception).lower())

    def test_audit_finding_regression_locked_with_no_credit_request(self):
        """The exact audit-finding bypass: locked customer + no
        floatra_order_id used to slip through the v3.0 before_submit
        gate because that gate only fires when floatra_order_id is
        set. The new validate hook must fail BEFORE the SO is even
        saved.
        """
        _ensure_customer("locked")
        # Explicitly: NO floatra_order_id, NO disbursement flag.
        so = _build_sales_order_doc()
        self.assertFalse(so.get("floatra_order_id"))
        with self.assertRaises(frappe.ValidationError):
            so.insert(ignore_permissions=True)

    def test_allows_save_when_customer_available(self):
        _ensure_customer("available")
        so = _build_sales_order_doc()
        # Should not raise.
        so.insert(ignore_permissions=True)
        self.assertTrue(so.name)

    def test_allows_save_when_customer_pending_kyc(self):
        # pending_kyc is a transient state, not 'locked' or 'suspended'.
        # Orders should still be saveable; the credit-request button
        # will catch the pending-KYC case separately.
        _ensure_customer("pending_kyc")
        so = _build_sales_order_doc()
        so.insert(ignore_permissions=True)
        self.assertTrue(so.name)

    def test_allows_save_when_customer_has_no_floatra_status(self):
        # A Customer with no Floatra fields populated yet — should
        # not raise. The gate is for explicitly Floatra-blocked
        # customers, not for unenrolled customers.
        _ensure_dependencies()
        unrelated = "Floatra Unrelated Customer"
        if not frappe.db.exists("Customer", unrelated):
            frappe.get_doc({
                "doctype": "Customer",
                "customer_name": unrelated,
                "customer_group": CUSTOMER_GROUP,
                "territory": TERRITORY,
            }).insert(ignore_permissions=True)
        so = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": unrelated,
            "delivery_date": frappe.utils.add_days(
                frappe.utils.nowdate(), 7,
            ),
            "items": [
                {
                    "item_code": ITEM_CODE,
                    "qty": 1,
                    "rate": 50000,
                    "delivery_date": frappe.utils.add_days(
                        frappe.utils.nowdate(), 7,
                    ),
                },
            ],
        })
        so.insert(ignore_permissions=True)
        self.assertTrue(so.name)
