# -*- coding: utf-8 -*-
"""after_install hook for floatra_credit.

Frappe's `install_app` calls `set_all_patches_as_completed` for fresh
installs, marking every patch in patches.txt as already-run *without*
executing it. That's the right behaviour for backwards-compat patches
(no need to re-run a v14 migration on a v15 install), but it means
our custom-field patches never apply on a fresh site.

We run them imperatively from `after_install` so the schema lands
regardless of install vs. migrate path. The underlying patch functions
are idempotent — `create_custom_fields` is upsert-y — so calling them
again on subsequent migrations is harmless.
"""

import json
import os

import frappe

from .patches import (
    add_floatra_fields_to_customer,
    add_floatra_fields_to_sales_order,
    add_floatra_category_to_item_group,
    add_tenure_days_to_sales_order,
    add_onboard_expanded_fields_to_customer,
    add_confirm_delivery_expanded_fields_to_sales_order,
    add_kyc_link_fields_to_customer,
    v0_3_0_partner_contract,
)


def after_install() -> None:
    add_floatra_fields_to_customer.execute()
    add_floatra_fields_to_sales_order.execute()
    add_floatra_category_to_item_group.execute()
    # P2 batch 9 follow-up: bench-tests exposed that three patches
    # registered in patches.txt never ran imperatively. Frappe marks
    # patches.txt entries as already-completed on fresh install
    # without running them, so the columns (owner/address blocks,
    # confirm-delivery fields, tenure_days) silently never landed on a
    # clean ERPNext site.
    # Running them here is idempotent (`create_custom_fields` is
    # upsert-y) and parallel to the four lines above.
    add_tenure_days_to_sales_order.execute()
    add_onboard_expanded_fields_to_customer.execute()
    add_confirm_delivery_expanded_fields_to_sales_order.execute()
    add_kyc_link_fields_to_customer.execute()
    v0_3_0_partner_contract.execute()
    _install_workspace()


def _install_workspace() -> None:
    """P2-14: register the Floatra workspace.

    Frappe normally auto-installs workspace fixtures from
    `<app>/<module>/workspace/<name>/<name>.json` on `bench
    install-app`, but the auto-load path is brittle (skipped on
    some upgrade paths, races with custom-field patches that touch
    the same DocTypes). Imperative install via `frappe.get_doc` is
    the same idempotent path the rest of `after_install` uses, so
    it lands every time.

    Idempotency: if the Workspace already exists we leave it alone
    rather than overwrite. Distributors who customised the
    workspace (re-ordered shortcuts, added a card) shouldn't lose
    that on `bench migrate`.
    """
    # Frappe names a Workspace by its title, so this is the doc name.
    workspace_name = "Floatra"
    if frappe.db.exists("Workspace", workspace_name):
        return

    fixture_path = os.path.join(
        os.path.dirname(__file__),
        "workspace",
        "floatra",
        "floatra.json",
    )
    if not os.path.exists(fixture_path):
        return

    with open(fixture_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    doc = frappe.get_doc(data)
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
