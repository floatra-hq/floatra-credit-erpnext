# -*- coding: utf-8 -*-
"""Frappe hooks for the Floatra app.

Mirrors the Odoo connector's wiring:
  - Custom-field patches run at install/migrate time so updates to
    ERPNext core don't blow away our additions.
  - Sales Order before_submit gate refuses to confirm if Floatra
    credit was requested but not yet disbursed (analogous to
    SaleOrder._check_floatra_disbursement_before_confirm in Odoo).
  - Whitelisted webhook receiver at
    `/api/method/floatra_credit.controllers.webhook.receive`.
  - Scheduler tasks for status sync + undelivered-webhook replay.

Reference: ERPNext apps load this file via the Frappe app loader.
The list of supported keys is documented at
https://frappeframework.com/docs/v15/user/en/python-api/hooks.
"""

# ---- App identity ----
app_name = "floatra_credit"
app_title = "Floatra"
app_publisher = "Floatra"
app_description = (
    "Floatra inventory financing for ERPNext — Mode A native connector "
    "(equivalent to the Odoo addon)."
)
app_email = "engineering@floatra.com"
app_license = "MIT"

# ---- Install / patches ----
# Patches are registered in patches.txt at the app root. On a fresh
# install Frappe marks every patches.txt entry as already-completed
# without running them (set_all_patches_as_completed), so the custom-
# field patches won't apply via the install path. The after_install
# hook below runs them imperatively, and the same patches still run
# via `bench migrate` on subsequent upgrades. Custom fields are
# applied via patches rather than fixture JSON so the fields survive
# ERPNext upgrades — the spec explicitly calls this out
# ("Custom fields survive ERPNext updates").
after_install = "floatra_credit.install.after_install"

# ---- Workspace (P2-14) ----
# Distributors had to use search to reach Floatra config before
# this — `Floatra Settings`, customer KYC, webhook log were all
# only accessible via the global search bar. The Workspace fixture
# JSON in `workspace/floatra/floatra.json` is inserted once by
# `install.after_install` (it sits outside the module directory, so
# `bench migrate` does not sync it and never overwrites a distributor's
# edits), registering a side-nav entry under the "Public" workspaces
# section with shortcuts for the main config + lookup views.
fixtures = [
    {
        "dt": "Workspace",
        "filters": [["module", "=", "Floatra Credit"]],
    },
]

# ---- Doc events ----
# Three Sales Order hooks form a two-layer Floatra gate:
#   validate       — fires on every save (draft, submit, modify). P0-15
#                    server-side reorder lock: refuse to save a Sales
#                    Order whose Customer is `locked` or `suspended`
#                    regardless of whether a credit request was
#                    ever made. Without this, a sales user who never
#                    clicked "Request Financing" could bypass the
#                    `before_submit` gate on a locked merchant.
#   before_submit  — the original disbursement gate. Refuses to submit
#                    a SO that has a credit request but no
#                    disbursement landed yet.
#   on_submit      — placeholder for future post-submit notification.
doc_events = {
    "Sales Order": {
        "validate": "floatra_credit.controllers.sales_order.validate",
        "before_submit": "floatra_credit.controllers.sales_order.before_submit",
        "on_submit": "floatra_credit.controllers.sales_order.on_submit",
        # P1-16: mirror cancels into Floatra so the gateway releases
        # lender capacity reservations on undisbursed approvals, and
        # refuses the cancel altogether on already-disbursed loans.
        "on_cancel": "floatra_credit.controllers.sales_order.on_cancel",
        # A draft is deleted, not cancelled: on_trash releases an approved,
        # unfunded reservation too.
        "on_trash": "floatra_credit.controllers.sales_order.on_trash",
    },
    "Customer": {
        "after_insert": "floatra_credit.controllers.customer.after_insert",
    },
}

# ---- Client scripts ----
# Loaded into the desk for Sales Order / Customer forms.
doctype_js = {
    "Sales Order": "public/js/sales_order.js",
    "Customer": "public/js/customer.js",
}

# ---- Scheduler events ----
# Two recurring jobs:
#   - sync_credit_limits: pull each customer's credit status from
#     Floatra. Keeps the desk view from drifting if a Floatra-side
#     decision happens outside an order flow.
#   - poll_undelivered_webhooks: replay events Floatra could not deliver.
scheduler_events = {
    "cron": {
        "0 */4 * * *": [
            "floatra_credit.tasks.sync_credit_limits",
        ],
        # P2-13: undelivered-webhook catch-up. Daily at 03:30 UTC
        # — late enough that the gateway's per-event 5-attempt
        # retry budget (~6h ceiling) has settled. Matches the
        # Odoo P1-13 cadence so both connectors fail in the same
        # way when ERPNext + Odoo run side-by-side.
        "30 3 * * *": [
            "floatra_credit.tasks.poll_undelivered_webhooks",
        ],
    }
}

# ---- Permissions ----
# Whitelisted methods callable from the client (frappe.call) or as
# raw HTTP via /api/method/<path>. The webhook receiver accepts
# `allow_guest=True` because Floatra signs requests rather than
# carrying an ERPNext session.
override_whitelisted_methods = {}
