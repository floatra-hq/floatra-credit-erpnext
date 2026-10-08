# -*- coding: utf-8 -*-
"""Test-suite teardown helper.

The integration tests insert rows under deterministic names (e.g.
event_id `evt-idempotent-1`, sales order references that survive
across reruns). Frappe's unittest TestCase doesn't roll back DB
state by default for non-TransactionCase tests, so this helper
clears the rows before a local rerun.

Run via `bench --site <site> execute floatra_credit.tests.integration._cleanup.clear`.
"""

import frappe


KNOWN_EVENT_IDS = [
    "test-duplicate-1",
    "evt-idempotent-1",
    "evt-db-unique-1",
    "evt-bad-sig-1",
    "evt-sig-1",
]


def clear() -> None:
    """Drop test-only rows so unique-key assertions can re-run."""
    for event_id in KNOWN_EVENT_IDS:
        rows = frappe.get_all(
            "Floatra Webhook Log",
            filters={"event_id": event_id},
            pluck="name",
        )
        for r in rows:
            frappe.delete_doc(
                "Floatra Webhook Log", r, ignore_permissions=True, force=True,
            )
    frappe.db.commit()
    # Test sales orders we created. Retired without the Floatra hooks: the
    # connector's on_cancel / on_trash call Floatra, which a test bench
    # cannot reach, so the Floatra decision is cleared first.
    for so in frappe.get_all(
        "Sales Order",
        filters={
            "customer": [
                "in",
                [
                    "Floatra Credit Test Customer",
                    "Floatra Category Test Customer",
                ],
            ],
        },
        pluck="name",
    ):
        frappe.db.set_value(
            "Sales Order", so,
            {"docstatus": 2, "floatra_decision": "not_requested", "floatra_disbursed": 0},
        )
        frappe.delete_doc(
            "Sales Order", so, ignore_permissions=True, force=True,
        )
    frappe.db.commit()
    print("Test rows cleared.")
