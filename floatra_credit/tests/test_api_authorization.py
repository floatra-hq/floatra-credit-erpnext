# -*- coding: utf-8 -*-
"""VULN-001 regression: the whitelisted api.py methods must enforce a
record/permission gate, not just authentication.

`@frappe.whitelist()` only requires a logged-in user, and
`frappe.get_doc()` does NOT enforce record permissions — so before this
fix any authenticated ERPNext user could initiate credit, record
repayments, change bank accounts, etc. on ARBITRARY Customers / Sales
Orders. Each method now calls `doc.check_permission("write")` (or
`frappe.has_permission(..., throw=True)` for the doc-less reconcile),
which raises `frappe.PermissionError` when the caller lacks access.

These tests assert the gate fires BEFORE any Floatra API side effect.
`frappe` is stubbed so they run without a bench.
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _install_frappe_stub() -> None:
    frappe = sys.modules.get("frappe")
    if frappe is None:
        frappe = types.ModuleType("frappe")

        def _whitelist(*args, **kwargs):
            # supports both @frappe.whitelist and @frappe.whitelist(...)
            if args and callable(args[0]):
                return args[0]

            def deco(func):
                return func

            return deco

        frappe.whitelist = _whitelist
        frappe._ = lambda s: s
        frappe.db = types.SimpleNamespace(
            set_value=MagicMock(),
            exists=MagicMock(return_value=True),
            get_value=MagicMock(),
            commit=MagicMock(),
        )
        frappe.get_single = MagicMock()
        frappe.utils = types.SimpleNamespace()
        sys.modules["frappe"] = frappe

    # Ensure the attributes these tests + the fix rely on exist.
    if not hasattr(frappe, "PermissionError"):
        class PermissionError(Exception):
            pass

        frappe.PermissionError = PermissionError
    if not hasattr(frappe, "throw"):
        frappe.throw = MagicMock(side_effect=RuntimeError)
    frappe.get_doc = MagicMock()
    frappe.has_permission = MagicMock(return_value=True)


from . import frappe_stub  # noqa: E402

frappe_stub.install()
if not frappe_stub.under_bench():
    _install_frappe_stub()
load_tests = frappe_stub.load_tests

from .. import api  # noqa: E402  (import after stub install)


def _denied_doc():
    """A doc whose check_permission('write') raises PermissionError."""
    doc = MagicMock()
    doc.check_permission.side_effect = api.frappe.PermissionError("no write perm")
    return doc


class TestWhitelistedMethodAuthorization(unittest.TestCase):
    def setUp(self):
        # Detect whether the Floatra API client is reached (it must NOT be
        # when the permission gate denies).
        self._client_patch = patch.object(api, "FloatraAPIClient")
        self.mock_client = self._client_patch.start()
        self.addCleanup(self._client_patch.stop)

    def _assert_blocked_before_api(self, call):
        with patch.object(api.frappe, "get_doc", return_value=_denied_doc()):
            with self.assertRaises(api.frappe.PermissionError):
                call()
        self.mock_client.for_site.assert_not_called()

    # ----- doc-scoped methods (check_permission on the loaded doc) -----

    def test_request_credit_requires_permission(self):
        self._assert_blocked_before_api(lambda: api.request_credit("SO-0001"))

    def test_record_cash_repayment_requires_permission(self):
        self._assert_blocked_before_api(
            lambda: api.record_cash_repayment("SO-0001", 1000, "REF-1"),
        )

    def test_confirm_delivery_requires_permission(self):
        self._assert_blocked_before_api(
            lambda: api.confirm_delivery_for_sales_order("SO-0001"),
        )

    def test_get_kyc_link_requires_permission(self):
        self._assert_blocked_before_api(
            lambda: api.action_get_kyc_link("CUST-0001"),
        )

    def test_initiate_return_requires_permission(self):
        self._assert_blocked_before_api(
            lambda: api.action_initiate_return("SO-0001", 500, "DAMAGED"),
        )

    def test_set_bank_account_requires_permission(self):
        self._assert_blocked_before_api(
            lambda: api.action_set_bank_account(
                "CUST-0001", "058", "0123456789", "Ada Kiosk",
            ),
        )

    # ----- doc-less method (has_permission gate) -----

    def test_reconcile_orders_requires_read_permission(self):
        with patch.object(
            api.frappe,
            "has_permission",
            side_effect=api.frappe.PermissionError("no read perm"),
        ):
            with self.assertRaises(api.frappe.PermissionError):
                api.action_reconcile_orders()
        self.mock_client.for_site.assert_not_called()

    # ----- positive: a permitted caller passes the gate -----

    def test_permitted_caller_passes_the_gate(self):
        # Grant permission (check_permission is a no-op MagicMock), then make
        # the SO submitted so request_credit throws the NEXT guard — proving
        # the permission gate did NOT block a legitimate caller.
        so = MagicMock()
        so.check_permission.return_value = None
        so.docstatus = 1  # submitted → the post-gate guard fires
        with patch.object(api.frappe, "get_doc", return_value=so):
            with self.assertRaises(RuntimeError):  # frappe.throw stub raises
                api.request_credit("SO-0001")
        so.check_permission.assert_called_once_with("write")
        # It got past auth to the business guard, not into the API client.
        self.mock_client.for_site.assert_not_called()


if __name__ == "__main__":
    unittest.main()
