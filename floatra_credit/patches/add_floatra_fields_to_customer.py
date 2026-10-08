# -*- coding: utf-8 -*-
"""Add Floatra fields to the Customer DocType.

We use a patch + `create_custom_fields` rather than a fixture JSON
override because fixtures get blown away on ERPNext upgrade; custom
fields registered via the patch survive.

Idempotent: `create_custom_fields` is upsert-y — re-running on a
site that already has the fields is a no-op.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute() -> None:
    fields = {
        "Customer": [
            {
                "fieldname": "floatra_section",
                "label": "Floatra",
                "fieldtype": "Section Break",
                "insert_after": "is_internal_customer",
                "collapsible": 1,
            },
            {
                "fieldname": "floatra_merchant_id",
                "label": "Floatra Merchant ID",
                "fieldtype": "Data",
                "insert_after": "floatra_section",
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "floatra_credit_status",
                "label": "Credit Status",
                "fieldtype": "Select",
                "options": "not_enrolled\npending_kyc\navailable\nlocked\nsuspended",
                "default": "not_enrolled",
                "insert_after": "floatra_merchant_id",
                "read_only": 1,
            },
            {
                "fieldname": "floatra_credit_limit",
                "label": "Credit Limit",
                "fieldtype": "Currency",
                "insert_after": "floatra_credit_status",
                "read_only": 1,
            },
            {
                "fieldname": "floatra_credit_tier",
                "label": "Credit Tier",
                "fieldtype": "Data",
                "insert_after": "floatra_credit_limit",
                "read_only": 1,
            },
            {
                "fieldname": "floatra_kyc_status",
                "label": "KYC Status",
                "fieldtype": "Select",
                "options": "not_started\npending\napproved\nrejected",
                "default": "not_started",
                "insert_after": "floatra_credit_tier",
                "read_only": 1,
            },
            {
                "fieldname": "floatra_last_status_check",
                "label": "Last Floatra Status Check",
                "fieldtype": "Datetime",
                "insert_after": "floatra_kyc_status",
                "read_only": 1,
                "hidden": 1,
            },
        ]
    }
    create_custom_fields(fields, ignore_validate=True)
    frappe.clear_cache(doctype="Customer")
