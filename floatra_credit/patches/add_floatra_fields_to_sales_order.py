# -*- coding: utf-8 -*-
"""Add Floatra fields to the Sales Order DocType."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute() -> None:
    fields = {
        "Sales Order": [
            {
                "fieldname": "floatra_section",
                "label": "Floatra",
                "fieldtype": "Section Break",
                "insert_after": "tax_id",
                "collapsible": 1,
            },
            {
                "fieldname": "floatra_order_id",
                "label": "Floatra Order ID",
                "fieldtype": "Data",
                "insert_after": "floatra_section",
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "floatra_decision",
                "label": "Decision",
                "fieldtype": "Select",
                "options": "not_requested\npending\napproved\ndeclined\nwaitlisted",
                "default": "not_requested",
                "insert_after": "floatra_order_id",
                "read_only": 1,
            },
            {
                "fieldname": "floatra_decision_reason",
                "label": "Decision Reason",
                "fieldtype": "Small Text",
                "insert_after": "floatra_decision",
                "read_only": 1,
            },
            {
                "fieldname": "floatra_approved_amount",
                "label": "Approved Amount",
                "fieldtype": "Currency",
                "insert_after": "floatra_decision_reason",
                "read_only": 1,
            },
            {
                "fieldname": "floatra_repayment_amount",
                "label": "Repayment Amount",
                "fieldtype": "Currency",
                "insert_after": "floatra_approved_amount",
                "read_only": 1,
            },
            {
                "fieldname": "floatra_repayment_due_date",
                "label": "Repayment Due Date",
                "fieldtype": "Date",
                "insert_after": "floatra_repayment_amount",
                "read_only": 1,
            },
            {
                "fieldname": "floatra_column_break",
                "fieldtype": "Column Break",
                "insert_after": "floatra_repayment_due_date",
            },
            {
                "fieldname": "floatra_disbursed",
                "label": "Disbursed",
                "fieldtype": "Check",
                "insert_after": "floatra_column_break",
                "read_only": 1,
                "no_copy": 1,
            },
            {
                "fieldname": "floatra_delivery_confirmed",
                "label": "Delivery Confirmed",
                "fieldtype": "Check",
                "insert_after": "floatra_disbursed",
                "read_only": 1,
                "no_copy": 1,
            },
        ]
    }
    create_custom_fields(fields, ignore_validate=True)
    frappe.clear_cache(doctype="Sales Order")
