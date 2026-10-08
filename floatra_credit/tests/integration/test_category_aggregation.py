# -*- coding: utf-8 -*-
"""Frappe-runtime test: `api._resolve_category_for` aggregation.

Verifies the algorithm that picks a Floatra OrderCategory from a
Sales Order's line items, weighted by revenue contribution:
  - For each line item, look up its Item Group's `floatra_category`
  - Sum the amount contribution per category
  - Pick the category with the highest total
  - Fall back to OTHER if no line has a mapping (P2-15)

The unit-test version is in test_contract / credit_lock tests
(behaviour-level). This file tests the SAME function against real
Frappe Items + Item Groups so we catch regressions in Frappe's
ORM behaviour or our query shape.
"""

import frappe
import unittest

from ...api import _resolve_category_for


CUSTOMER_NAME = "Floatra Category Test Customer"
CUSTOMER_GROUP = "Floatra Category Test Customer Group"
TERRITORY = "Floatra Category Test Territory"


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
            "floatra_merchant_id": "MERCH-CAT-TEST",
        }).insert(ignore_permissions=True)


def _ensure_item_group(name: str, floatra_category: str | None = None):
    if not frappe.db.exists("Item Group", name):
        ig = frappe.get_doc({
            "doctype": "Item Group",
            "item_group_name": name,
            "parent_item_group": "All Item Groups",
            "is_group": 0,
        }).insert(ignore_permissions=True)
        if floatra_category:
            frappe.db.set_value(
                "Item Group", ig.name, "floatra_category", floatra_category,
            )
    elif floatra_category:
        frappe.db.set_value(
            "Item Group", name, "floatra_category", floatra_category,
        )


def _ensure_item(code: str, item_group: str):
    if not frappe.db.exists("Item", code):
        frappe.get_doc({
            "doctype": "Item",
            "item_code": code,
            "item_name": code,
            "item_group": item_group,
            "is_stock_item": 0,
            "stock_uom": "Nos",
        }).insert(ignore_permissions=True)


def _make_so(lines: list) -> "frappe.Document":
    _ensure_customer()
    so = frappe.get_doc({
        "doctype": "Sales Order",
        "customer": CUSTOMER_NAME,
        "delivery_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
        "items": [
            {
                "item_code": line["item_code"],
                "qty": line["qty"],
                "rate": line["rate"],
                "delivery_date": frappe.utils.add_days(
                    frappe.utils.nowdate(), 7,
                ),
            }
            for line in lines
        ],
    })
    so.insert(ignore_permissions=True)
    return so


class TestCategoryAggregation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Build three Item Groups with distinct Floatra categories.
        # P2-15: aligned to the gateway's OrderCategory enum
        # (PHARMA → PHARMACY, BUILDING_MATERIALS → OTHER).
        _ensure_item_group("Cat-Beverages-Test", "FMCG")
        _ensure_item_group("Cat-Pharma-Test", "PHARMACY")
        _ensure_item_group("Cat-Building-Test", "OTHER")
        _ensure_item_group("Cat-Unmapped-Test", None)
        _ensure_item("CAT-BEV-1", "Cat-Beverages-Test")
        _ensure_item("CAT-PHA-1", "Cat-Pharma-Test")
        _ensure_item("CAT-BLD-1", "Cat-Building-Test")
        _ensure_item("CAT-UNM-1", "Cat-Unmapped-Test")

    def test_single_category_resolves_to_that_category(self):
        so = _make_so(
            [{"item_code": "CAT-BEV-1", "qty": 1, "rate": 50000}],
        )
        self.assertEqual(_resolve_category_for(so), "FMCG")

    def test_highest_revenue_category_wins(self):
        # FMCG: 1×50000 = 50000. PHARMACY: 1×80000 = 80000. → PHARMACY
        so = _make_so([
            {"item_code": "CAT-BEV-1", "qty": 1, "rate": 50000},
            {"item_code": "CAT-PHA-1", "qty": 1, "rate": 80000},
        ])
        self.assertEqual(_resolve_category_for(so), "PHARMACY")

    def test_quantity_factors_into_weight(self):
        # FMCG: 10×10000 = 100000. PHARMACY: 1×80000 = 80000. → FMCG
        so = _make_so([
            {"item_code": "CAT-BEV-1", "qty": 10, "rate": 10000},
            {"item_code": "CAT-PHA-1", "qty": 1, "rate": 80000},
        ])
        self.assertEqual(_resolve_category_for(so), "FMCG")

    def test_unmapped_item_groups_ignored(self):
        # Unmapped contributes 0 to all weight buckets; FMCG wins.
        so = _make_so([
            {"item_code": "CAT-BEV-1", "qty": 1, "rate": 50000},
            {"item_code": "CAT-UNM-1", "qty": 1, "rate": 200000},
        ])
        self.assertEqual(_resolve_category_for(so), "FMCG")

    def test_fallback_to_other_when_no_lines_have_category(self):
        # P2-15: default was GENERAL_TRADE which 422'd at the
        # gateway. OTHER is the catch-all in OrderCategory.
        so = _make_so(
            [{"item_code": "CAT-UNM-1", "qty": 1, "rate": 50000}],
        )
        self.assertEqual(_resolve_category_for(so), "OTHER")
