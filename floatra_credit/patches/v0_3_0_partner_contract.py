# -*- coding: utf-8 -*-
"""0.3.0: align the app's fields and settings with Floatra's partner API.

* Sales Order gains ``floatra_loan_id`` (Floatra's LOAN id: every lifecycle
  call and loan webhook uses it) and ``floatra_interest_amount``.
* Retired fields are removed: Customer ``floatra_bvn`` / ``floatra_nin``
  (Floatra ignores identity numbers at onboarding; KYC runs on the hosted
  link; the stored values are blanked before the field goes), Sales Order
  ``floatra_is_test`` (Floatra rejects ``is_test``; use the sandbox key) and
  ``floatra_approval_expires_at`` (Floatra retired the 2-hour window).
* BVN/NIN entries are stripped from the Customer's ``tabVersion`` history
  (Customer tracks changes, so every edit of those fields was copied there).
* Sales Order gains ``floatra_loan_request_id`` (a waitlisted order's later
  approval/decline webhook names it).
* ``Floatra Platform Config.floatra_gateway_url`` becomes host-only
  (``https://api.floatra.com``): a trailing ``/v1`` is stripped and the
  old ``*.floatra.io`` default, which never resolved, is replaced.

Idempotent. Sales Orders created before 0.3.0 get their loan id looked up
by order reference the first time it is needed (``api._loan_id_for``).
"""

import json

import frappe

from ..services.floatra_contract import normalize_base_url

_PII_FIELDS = frozenset({"floatra_bvn", "floatra_nin"})

_RETIRED_FIELDS = (
    ("Customer", "floatra_bvn", True),
    ("Customer", "floatra_nin", True),
    ("Sales Order", "floatra_is_test", False),
    ("Sales Order", "floatra_approval_expires_at", False),
)


def execute() -> None:
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    create_custom_fields(
        {
            "Sales Order": [
                {
                    "fieldname": "floatra_loan_id",
                    "label": "Floatra Loan ID",
                    "fieldtype": "Data",
                    "insert_after": "floatra_order_id",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": "floatra_loan_request_id",
                    "label": "Floatra Loan Request ID",
                    "fieldtype": "Data",
                    "insert_after": "floatra_loan_id",
                    "read_only": 1,
                    "no_copy": 1,
                    "search_index": 1,
                },
                {
                    "fieldname": "floatra_interest_amount",
                    "label": "Floatra Interest",
                    "fieldtype": "Currency",
                    "insert_after": "floatra_approved_amount",
                    "read_only": 1,
                    "no_copy": 1,
                },
            ]
        },
        ignore_validate=True,
    )
    for doctype, fieldname, is_pii in _RETIRED_FIELDS:
        if is_pii and frappe.db.has_column(doctype, fieldname):
            frappe.db.sql(f"UPDATE `tab{doctype}` SET `{fieldname}` = NULL")
        custom_field = f"{doctype}-{fieldname}"
        if frappe.db.exists("Custom Field", custom_field):
            frappe.delete_doc("Custom Field", custom_field, ignore_permissions=True, force=True)

    _strip_identity_numbers_from_versions()

    current = frappe.db.get_single_value("Floatra Platform Config", "floatra_gateway_url")
    normalized = normalize_base_url(current)
    if current != normalized:
        frappe.db.set_single_value(
            "Floatra Platform Config", "floatra_gateway_url", normalized,
        )
    for doctype in ("Customer", "Sales Order", "Floatra Platform Config"):
        frappe.clear_cache(doctype=doctype)


def strip_pii_from_version_data(data: str):
    """A ``tabVersion.data`` JSON with BVN/NIN entries removed, or None when
    there is nothing to remove (so re-running changes nothing)."""
    try:
        doc = json.loads(data or "{}")
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    changed = False
    for key in ("changed", "added", "removed"):
        rows = doc.get(key)
        if not isinstance(rows, list):
            continue
        kept = [
            row for row in rows
            if not (isinstance(row, (list, tuple)) and row and row[0] in _PII_FIELDS)
        ]
        if len(kept) != len(rows):
            doc[key] = kept
            changed = True
    return json.dumps(doc, separators=(",", ":")) if changed else None


def _strip_identity_numbers_from_versions() -> None:
    """Customer has change tracking in ERPNext, so BVN/NIN edits were also
    written to ``tabVersion``. Remove those entries (idempotent)."""
    if not frappe.get_meta("Customer").track_changes:
        return
    rows = frappe.db.sql(
        """SELECT name, data FROM `tabVersion`
           WHERE ref_doctype = 'Customer'
             AND (data LIKE %s OR data LIKE %s)""",
        ("%floatra_bvn%", "%floatra_nin%"),
        as_dict=True,
    )
    for row in rows:
        cleaned = strip_pii_from_version_data(row.data)
        if cleaned is not None:
            frappe.db.set_value("Version", row.name, "data", cleaned, update_modified=False)
