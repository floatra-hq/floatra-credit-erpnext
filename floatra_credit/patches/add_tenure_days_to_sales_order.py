# -*- coding: utf-8 -*-
"""D-11: Add `floatra_tenure_days` to Sales Order.

Spec §3.7 limits the merchant-facing tenure set to {14, 30}. Before
this patch ERPNext hard-coded 14 inside the API client, so a
30-day order couldn't be requested from the desk. Adds a Select
field with the two valid options; the api.py initiator forwards the
value to /v1/orders/initiate.

Default: 14 (T0/T1 baseline) so existing pilots see no behaviour
change.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute() -> None:
    fields = {
        "Sales Order": [
            {
                "fieldname": "floatra_tenure_days",
                "label": "Floatra Tenure",
                "fieldtype": "Select",
                "options": "14\n30",
                "default": "14",
                "insert_after": "floatra_is_test",
                "description": (
                    "Repayment tenure in days. Floatra spec §3.7 "
                    "accepts only 14 or 30 — anything else is rejected "
                    "by the gateway."
                ),
            },
        ]
    }
    create_custom_fields(fields, ignore_validate=True)
    frappe.clear_cache(doctype="Sales Order")
