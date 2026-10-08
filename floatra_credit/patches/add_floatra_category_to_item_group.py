# -*- coding: utf-8 -*-
"""Add Floatra category to Item Group.

The translator-side category mapping (Floatra needs an `OrderCategory`
enum value per loan) reads `item_group.floatra_category` to aggregate
across the Sales Order's lines. The default `OTHER` is applied if no
line has a category set (post-P2-15 — was `GENERAL_TRADE` before but
that drifted from the gateway-side enum).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


# Keep in sync with prisma/schema.prisma::OrderCategory + the
# gateway's @IsEnum(OrderCategory) validator on /v1/orders/initiate.
#
# P2-15: the prior list (PHARMA / BUILDING_MATERIALS / AGRICULTURAL_INPUTS
# / GENERAL_TRADE) drifted from the gateway-side enum, which would
# 422 a Sales Order whose dominant line carried any of those values.
# Aligned to the actual `OrderCategory` source of truth so the
# distributor's dropdown can't pick a value the gateway will reject.
FLOATRA_CATEGORY_OPTIONS = "\n".join(
    [
        "",  # blank — represents "no mapping yet"
        "FMCG",
        "ELECTRONICS",
        "FASHION",
        "AGRICULTURE",
        "PHARMACY",
        "OTHER",
    ]
)


def execute() -> None:
    fields = {
        "Item Group": [
            {
                "fieldname": "floatra_category",
                "label": "Floatra Category",
                "fieldtype": "Select",
                "options": FLOATRA_CATEGORY_OPTIONS,
                "insert_after": "parent_item_group",
                "description": (
                    "Maps this Item Group to a Floatra OrderCategory for "
                    "credit decisions. Leave blank to fall back to OTHER."
                ),
            },
        ]
    }
    create_custom_fields(fields, ignore_validate=True)
    frappe.clear_cache(doctype="Item Group")
