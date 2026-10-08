# -*- coding: utf-8 -*-
"""Floatra Cash Repayment Wizard — DocType controller.

Transient doctype: rows are never persisted long-term. The wizard
exists purely as a form surface for posting a manual repayment to
Floatra.

Mirrors `connectors/odoo/floatra_credit/wizards/cash_repayment_wizard.py`
field-for-field. The desk flow is:

  1. User opens the wizard from a Customer or Sales Order form.
  2. Form is pre-populated (customer / sales_order / outstanding).
  3. User enters amount + reference + source + collected_at + notes.
  4. Click submit → wizard's whitelisted method validates,
     calls FloatraAPIClient.record_repayment, posts a Comment on
     the Sales Order, and returns success.

Idempotency: Floatra-side dedup is via `reference`. The wizard
doesn't enforce its own — re-clicking submit with the same
reference is a safe no-op on Floatra core (returns the original
repayment record).
"""

from frappe.model.document import Document


class FloatraCashRepaymentWizard(Document):
    pass
