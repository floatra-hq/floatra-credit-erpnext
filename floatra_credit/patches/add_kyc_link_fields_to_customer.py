# -*- coding: utf-8 -*-
"""Hosted KYC link fields on Customer (connector 0.2.0).

`POST /v1/partner/merchants/:id/kyc/retry` returns a single-use, 24h
hosted KYC link in production. `api.action_get_kyc_link` stores it here
so desk users can copy or email it to the merchant; the
`merchant.kyc_completed` webhook clears it. Mirrors the Odoo
`res.partner.floatra_kyc_url` / `floatra_kyc_url_expires_at` fields.

Idempotent: `create_custom_fields` upserts. Safe to re-run.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute() -> None:
    fields = {
        "Customer": [
            {
                "fieldname": "floatra_kyc_url",
                "label": "KYC Link",
                "fieldtype": "Small Text",
                "insert_after": "floatra_kyc_status",
                "read_only": 1,
                "no_copy": 1,
                "description": (
                    "Floatra's hosted KYC link for this merchant (Floatra → "
                    "Get KYC Link). Send it to the merchant: they open it on "
                    "their phone, confirm a code sent to their phone number "
                    "on Floatra, enter their NIN or BVN and take a selfie. "
                    "Single use; getting a new link revokes this one."
                ),
            },
            {
                "fieldname": "floatra_kyc_url_expires_at",
                "label": "KYC Link Expires",
                "fieldtype": "Datetime",
                "insert_after": "floatra_kyc_url",
                "read_only": 1,
                "no_copy": 1,
                "depends_on": "eval:doc.floatra_kyc_url",
            },
        ]
    }
    create_custom_fields(fields, ignore_validate=True)
    frappe.clear_cache(doctype="Customer")
