# -*- coding: utf-8 -*-
"""P1-15 phase 3: ERP spec §3.2 expanded onboard fields on Customer.

Mirrors what we added to res.partner on the Odoo side (#295). Surfaces
the fields the Floatra spec wants partners to send during merchant
onboarding so distributors capture them in the desk and the
floatra_api client reads them at onboard time.

Idempotent: `create_custom_fields` upserts. Safe to re-run.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute() -> None:
    fields = {
        "Customer": [
            {
                "fieldname": "floatra_onboard_section",
                "label": "Floatra Onboard Profile (Spec §3.2)",
                "fieldtype": "Section Break",
                "insert_after": "floatra_last_status_check",
                "collapsible": 1,
            },
            {
                "fieldname": "floatra_business_type",
                "label": "Business Type",
                "fieldtype": "Select",
                "options": "\nRETAILER\nWHOLESALER\nKIOSK",
                "insert_after": "floatra_onboard_section",
                "description": (
                    "Spec §3.2 business_type. Defaults the merchant's "
                    "trade segment for Floatra's underwriting model."
                ),
            },
            {
                "fieldname": "floatra_primary_category",
                "label": "Primary Category",
                "fieldtype": "Select",
                "options": (
                    "\nFMCG\nELECTRONICS\nFASHION\nAGRICULTURE\n"
                    "PHARMACY\nOTHER"
                ),
                "insert_after": "floatra_business_type",
                "description": (
                    "Spec §3.2 category. Default Floatra OrderCategory "
                    "for subsequent /v1/orders/initiate calls."
                ),
            },
            {
                "fieldname": "floatra_assigned_agent_id",
                "label": "Assigned Agent ID",
                "fieldtype": "Data",
                "insert_after": "floatra_primary_category",
                "description": (
                    "Spec §3.2 assigned_agent_id. The field agent the "
                    "distributor has assigned to this merchant."
                ),
            },
            {
                "fieldname": "floatra_owner_column_break",
                "fieldtype": "Column Break",
                "insert_after": "floatra_assigned_agent_id",
            },
            {
                "fieldname": "floatra_owner_first_name",
                "label": "Owner First Name",
                "fieldtype": "Data",
                "insert_after": "floatra_owner_column_break",
            },
            {
                "fieldname": "floatra_owner_last_name",
                "label": "Owner Last Name",
                "fieldtype": "Data",
                "insert_after": "floatra_owner_first_name",
            },
        ]
    }
    create_custom_fields(fields, ignore_validate=True)
    frappe.clear_cache(doctype="Customer")
