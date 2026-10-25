# -*- coding: utf-8 -*-
"""Restrict the hosted KYC link fields to Customer editors (connector 0.3.3).

`Customer.floatra_kyc_url` is a single-use bearer link to the merchant's KYC
session. At permission level 0 every role that can READ a Customer saw it.
Only roles that can WRITE a Customer can mint one (`action_get_kyc_link`
checks `write`), so only they need to see it.

Order matters: grant level-1 read to every Customer writer FIRST, then raise
the fields to level 1, so no editor loses the link between the two steps.

Writers are the roles holding an unconditional (not owner-only) level-0
`write` on Customer. Frappe answers permissions from Custom DocPerm once any
row exists for a doctype, so `add_permission` first copies the standard
DocPerms (`setup_custom_perms`) — the existing permissions are preserved,
and the site's Role Permission Manager shows the result.

Idempotent: an existing level-1 rule is left alone; the field update upserts.
"""

import frappe
from frappe.permissions import add_permission, setup_custom_perms

from . import add_kyc_link_fields_to_customer

CUSTOMER = "Customer"


def customer_writer_roles() -> list:
    """Roles with unconditional level-0 write on Customer (post-copy)."""
    setup_custom_perms(CUSTOMER)
    rows = frappe.get_all(
        "Custom DocPerm",
        filters={"parent": CUSTOMER, "permlevel": 0, "write": 1, "if_owner": 0},
        pluck="role",
    )
    return sorted(set(rows))


def _has_level_rule(role: str, permlevel: int) -> bool:
    return bool(
        frappe.db.get_value(
            "Custom DocPerm",
            {"parent": CUSTOMER, "role": role, "permlevel": permlevel, "if_owner": 0},
        )
    )


def execute() -> None:
    level = add_kyc_link_fields_to_customer.KYC_LINK_PERMLEVEL
    for role in customer_writer_roles():
        if not _has_level_rule(role, level):
            add_permission(CUSTOMER, role, permlevel=level, ptype="read")
    add_kyc_link_fields_to_customer.execute()
    frappe.clear_cache(doctype=CUSTOMER)
