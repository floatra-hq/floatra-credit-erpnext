# -*- coding: utf-8 -*-
"""P1-15 phase 3: ERP spec §3.3 expanded confirm-delivery fields on
Sales Order.

Mirrors the Odoo wizard fields added in #295. Distributors capture
the GPS sample / proof-of-delivery URL / confirmation method on the
Sales Order itself (no separate delivery wizard exists in ERPNext —
the desk "Confirm Delivery" dialog reads these fields when invoked).

Idempotent: `create_custom_fields` upserts.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute() -> None:
    fields = {
        "Sales Order": [
            {
                "fieldname": "floatra_delivery_section",
                "label": "Floatra Delivery Context (Spec §3.3)",
                "fieldtype": "Section Break",
                "insert_after": "floatra_tenure_days",
                "collapsible": 1,
            },
            {
                "fieldname": "floatra_agent_name",
                "label": "Agent Name (Delivering)",
                "fieldtype": "Data",
                "insert_after": "floatra_delivery_section",
                "description": (
                    "Spec §3.3 confirmed_by.agent_name. Captured at "
                    "delivery time so Floatra's audit log shows the "
                    "human name without a downstream join."
                ),
            },
            {
                "fieldname": "floatra_confirmation_method",
                "label": "Confirmation Method",
                "fieldtype": "Select",
                "options": "\nAGENT_APP\nERP_CONSOLE\nGPS_VERIFIED",
                "default": "ERP_CONSOLE",
                "insert_after": "floatra_agent_name",
                "description": (
                    "Spec §3.3 confirmed_by.confirmation_method."
                ),
            },
            {
                "fieldname": "floatra_proof_of_delivery_url",
                "label": "Proof of Delivery URL",
                "fieldtype": "Data",
                "insert_after": "floatra_confirmation_method",
                "description": (
                    "Spec §3.3 proof_of_delivery_url. Hosted image / "
                    "PDF — typically a signed waybill or merchant selfie."
                ),
            },
            {
                "fieldname": "floatra_delivery_location_break",
                "fieldtype": "Column Break",
                "insert_after": "floatra_proof_of_delivery_url",
            },
            {
                "fieldname": "floatra_delivery_lat",
                "label": "Delivery Latitude",
                "fieldtype": "Float",
                "precision": "6",
                "insert_after": "floatra_delivery_location_break",
                "description": (
                    "Spec §3.3 location.lat. GPS sample at delivery."
                ),
            },
            {
                "fieldname": "floatra_delivery_lng",
                "label": "Delivery Longitude",
                "fieldtype": "Float",
                "precision": "6",
                "insert_after": "floatra_delivery_lat",
            },
            {
                "fieldname": "floatra_delivery_accuracy_meters",
                "label": "GPS Accuracy (m)",
                "fieldtype": "Int",
                "insert_after": "floatra_delivery_lng",
                "description": (
                    "Spec §3.3 location.accuracy_meters."
                ),
            },
            {
                "fieldname": "floatra_delivery_location_at",
                "label": "Location Recorded At",
                "fieldtype": "Datetime",
                "insert_after": "floatra_delivery_accuracy_meters",
                "description": (
                    "Spec §3.3 location.recorded_at."
                ),
            },
        ]
    }
    create_custom_fields(fields, ignore_validate=True)
    frappe.clear_cache(doctype="Sales Order")
