# -*- coding: utf-8 -*-
"""Floatra Webhook Log — append-only event ledger.

One row per inbound webhook. The `event_id` field carries Floatra's
`X-Floatra-Event-ID` header value and is the unique key — duplicate
deliveries (Floatra retries on 5xx) short-circuit on the
`frappe.db.exists("Floatra Webhook Log", event_id)` check inside
`controllers/webhook.py::receive`.

Equivalent to the Odoo connector's `floatra.webhook.log` model.
"""

from frappe.model.document import Document


class FloatraWebhookLog(Document):
    pass
