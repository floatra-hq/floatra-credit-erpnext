# -*- coding: utf-8 -*-
"""Floatra Platform Config — singleton DocType.

Holds the gateway URL, API key, platform ID, and webhook secret for
this ERPNext site. Configured by a System Manager via the desk:
  Floatra → Configuration

Equivalent to the Odoo connector's `ir.config_parameter`-backed
config in `services/floatra_api.py::FloatraAPIClient.for_env`.
"""

from frappe.model.document import Document


class FloatraPlatformConfig(Document):
    """Single instance — Frappe stores it in `tabSingles`."""

    pass
