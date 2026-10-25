# -*- coding: utf-8 -*-
"""The hosted KYC link is visible only to Customer editors (0.3.3).

`floatra_kyc_url` is a single-use bearer link. At permission level 0 every
Customer reader saw it; only roles with write on Customer can mint one, so
only they get permission level 1. Runs without a bench (stubbed frappe).
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, call

from . import frappe_stub
from .frappe_stub import load_tests  # noqa: F401  (no tests under bench)

frappe = frappe_stub.install()

if not frappe_stub.under_bench():
    perms = types.ModuleType("frappe.permissions")
    perms.add_permission = MagicMock()
    perms.setup_custom_perms = MagicMock()
    sys.modules["frappe.permissions"] = perms
    for name in ("frappe.custom", "frappe.custom.doctype", "frappe.custom.doctype.custom_field"):
        sys.modules.setdefault(name, types.ModuleType(name))
    cf = types.ModuleType("frappe.custom.doctype.custom_field.custom_field")
    cf.create_custom_fields = MagicMock()
    sys.modules["frappe.custom.doctype.custom_field.custom_field"] = cf
    frappe.clear_cache = MagicMock()

from floatra_credit.patches import (  # noqa: E402
    add_kyc_link_fields_to_customer as field_patch,
    v0_3_3_restrict_kyc_link_fields as restrict,
)


class TestKycLinkFieldsArePermlevel1(unittest.TestCase):
    def setUp(self):
        field_patch.create_custom_fields.reset_mock()

    def test_both_link_fields_sit_at_level_1(self):
        field_patch.execute()
        fields = field_patch.create_custom_fields.call_args[0][0]["Customer"]
        by_name = {f["fieldname"]: f for f in fields}
        self.assertEqual(field_patch.KYC_LINK_PERMLEVEL, 1)
        for name in ("floatra_kyc_url", "floatra_kyc_url_expires_at"):
            self.assertEqual(by_name[name]["permlevel"], 1, name)


class TestRestrictPatch(unittest.TestCase):
    def setUp(self):
        self.order = MagicMock()
        restrict.setup_custom_perms.reset_mock()
        restrict.add_permission.reset_mock()
        field_patch.create_custom_fields.reset_mock()
        self.order.attach_mock(restrict.add_permission, "grant")
        self.order.attach_mock(field_patch.create_custom_fields, "fields")
        frappe.get_all = MagicMock(return_value=["Sales User", "Sales Master Manager", "Sales User"])
        frappe.db.get_value = MagicMock(return_value=None)

    def test_writers_are_unconditional_level_0_write_after_copying_standard_perms(self):
        self.assertEqual(
            restrict.customer_writer_roles(), ["Sales Master Manager", "Sales User"],
        )
        restrict.setup_custom_perms.assert_called_once_with("Customer")
        filters = frappe.get_all.call_args.kwargs["filters"]
        self.assertEqual(
            filters,
            {"parent": "Customer", "permlevel": 0, "write": 1, "if_owner": 0},
        )

    def test_grants_level_1_read_to_every_writer_before_raising_the_fields(self):
        restrict.execute()

        grants = [c for c in self.order.mock_calls if c[0] == "grant"]
        self.assertEqual(
            grants,
            [
                call.grant("Customer", "Sales Master Manager", permlevel=1, ptype="read"),
                call.grant("Customer", "Sales User", permlevel=1, ptype="read"),
            ],
        )
        names = [c[0] for c in self.order.mock_calls]
        self.assertLess(
            max(i for i, n in enumerate(names) if n == "grant"),
            names.index("fields"),
            "level-1 read must be granted before the fields move to level 1",
        )

    def test_an_existing_level_1_rule_is_left_alone(self):
        frappe.db.get_value = MagicMock(
            side_effect=lambda dt, f: "perm-1" if f["role"] == "Sales User" else None,
        )
        restrict.execute()
        restrict.add_permission.assert_called_once_with(
            "Customer", "Sales Master Manager", permlevel=1, ptype="read",
        )
        field_patch.create_custom_fields.assert_called_once()

    def test_registered_for_migrate_and_fresh_install(self):
        import os

        app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(app, "patches.txt"), encoding="utf-8") as f:
            self.assertIn("floatra_credit.patches.v0_3_3_restrict_kyc_link_fields", f.read())
        with open(os.path.join(app, "install.py"), encoding="utf-8") as f:
            self.assertIn("v0_3_3_restrict_kyc_link_fields.execute()", f.read())


if __name__ == "__main__":
    unittest.main()
