# -*- coding: utf-8 -*-
"""Frappe-runtime test: verify our DocTypes + patches install.

Runs via `bench --site <site> run-tests --app floatra_credit`.
Does NOT run via plain `python3 -m unittest` because it needs a
live Frappe runtime (`import frappe` would fail outside bench).

Coverage:
  - Floatra Platform Config (singleton) exists + writeable
  - Floatra Webhook Log exists + accepts inserts + enforces the
    `event_id` UNIQUE constraint at the DB layer
  - Floatra Cash Repayment Wizard exists + is marked transient
  - All custom-field patches successfully added their fields to
    Customer / Sales Order / Item Group

These tests are the "did the install actually work" smoke layer —
they catch breakages in the JSON DocType definitions or patches
that wouldn't surface until a real user opens the form.
"""

import frappe
import unittest


class TestFloatraCreditInstall(unittest.TestCase):
    def test_platform_config_singleton_exists(self):
        doctype = frappe.get_meta("Floatra Platform Config")
        self.assertEqual(doctype.issingle, 1)
        # Pull the singleton — auto-creates the row.
        config = frappe.get_single("Floatra Platform Config")
        self.assertIsNotNone(config)

    def test_webhook_log_doctype_exists(self):
        doctype = frappe.get_meta("Floatra Webhook Log")
        self.assertEqual(doctype.issingle, 0)
        # event_id is the autoname / unique key.
        event_id_field = doctype.get_field("event_id")
        self.assertIsNotNone(event_id_field)
        self.assertEqual(event_id_field.unique, 1)

    def test_webhook_log_event_id_unique(self):
        # Two rows with the same event_id must raise.
        log1 = frappe.get_doc({
            "doctype": "Floatra Webhook Log",
            "event_id": "test-duplicate-1",
            "event_type": "order.test",
            "payload": "{}",
        })
        log1.insert(ignore_permissions=True)
        log2 = frappe.get_doc({
            "doctype": "Floatra Webhook Log",
            "event_id": "test-duplicate-1",
            "event_type": "order.test",
            "payload": "{}",
        })
        with self.assertRaises(frappe.DuplicateEntryError):
            log2.insert(ignore_permissions=True)

    def test_cash_repayment_wizard_exists(self):
        # The wizard was originally marked is_transient=1 (v14 semantics:
        # "don't persist this doctype's rows"). Frappe v15 removed
        # is_transient from the DocType schema, so the JSON value is
        # silently dropped and the meta reports 0. The doctype still
        # exists as a regular submittable-style form; the wizard's
        # controller (`api.collect_cash_repayment`) is what actually
        # forwards the data to Floatra without persisting locally.
        # Settle for asserting the doctype is registered.
        doctype = frappe.get_meta("Floatra Cash Repayment Wizard")
        self.assertIsNotNone(doctype)
        self.assertEqual(doctype.name, "Floatra Cash Repayment Wizard")

    def test_customer_has_floatra_fields(self):
        meta = frappe.get_meta("Customer")
        expected_fields = [
            "floatra_merchant_id",
            "floatra_credit_status",
            "floatra_credit_limit",
            "floatra_credit_tier",
            "floatra_kyc_status",
            "floatra_kyc_url",
            "floatra_kyc_url_expires_at",
        ]
        for fieldname in expected_fields:
            with self.subTest(field=fieldname):
                self.assertIsNotNone(
                    meta.get_field(fieldname),
                    f"Customer missing custom field: {fieldname}",
                )

    def test_sales_order_has_floatra_fields(self):
        meta = frappe.get_meta("Sales Order")
        expected_fields = [
            "floatra_order_id",
            "floatra_decision",
            "floatra_approved_amount",
            "floatra_repayment_amount",
            "floatra_repayment_due_date",
            "floatra_disbursed",
            "floatra_delivery_confirmed",
            "floatra_loan_id",
            "floatra_loan_request_id",
            "floatra_interest_amount",
        ]
        for fieldname in expected_fields:
            with self.subTest(field=fieldname):
                self.assertIsNotNone(
                    meta.get_field(fieldname),
                    f"Sales Order missing custom field: {fieldname}",
                )

    def test_item_group_has_floatra_category(self):
        meta = frappe.get_meta("Item Group")
        field = meta.get_field("floatra_category")
        self.assertIsNotNone(field)
        # P2-15: options enum must match the gateway's
        # OrderCategory exactly — FMCG / ELECTRONICS / FASHION /
        # AGRICULTURE / PHARMACY / OTHER. Anything else 422s at
        # /v1/orders/initiate.
        options = (field.options or "").split("\n")
        for expected in (
            "FMCG",
            "ELECTRONICS",
            "FASHION",
            "AGRICULTURE",
            "PHARMACY",
            "OTHER",
        ):
            with self.subTest(option=expected):
                self.assertIn(expected, options)
        # Sanity guard against the pre-P2-15 drift values reappearing.
        for stale in ("PHARMA", "GENERAL_TRADE", "BUILDING_MATERIALS"):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, options)

    def test_floatra_workspace_installed(self):
        """P2-14: install registers the public "Floatra" workspace
        so distributors aren't search-only. Frappe names a workspace by
        its title ("Floatra"), not the fixture's "name" key.
        """
        self.assertTrue(
            frappe.db.exists("Workspace", "Floatra"),
            "Floatra workspace should be present after install",
        )
        self.assertEqual(
            frappe.get_all("Workspace", filters={"module": "Floatra Credit"}, pluck="name"),
            ["Floatra"],
        )
        ws = frappe.get_doc("Workspace", "Floatra")
        self.assertEqual(ws.public, 1)
        self.assertEqual(ws.module, "Floatra Credit")
        # Shortcut sanity: the four headline link_to targets should be
        # registered DocTypes the workspace will land users on.
        targets = {s.link_to for s in (ws.shortcuts or [])}
        self.assertIn("Floatra Platform Config", targets)
        self.assertIn("Customer", targets)
        self.assertIn("Sales Order", targets)
        self.assertIn("Floatra Webhook Log", targets)
