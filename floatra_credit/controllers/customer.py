# -*- coding: utf-8 -*-
"""Customer doc-event handlers."""

import logging

from ..services.floatra_api import ContractError, FloatraAPIError

_logger = logging.getLogger(__name__)


def after_insert(doc, method=None) -> None:
    """Best-effort Floatra onboarding when a Customer is created.

    A Floatra outage or incomplete Customer data (e.g. no Nigerian mobile
    number) must not block creating the Customer: the failure is logged and
    staff retry with **Floatra → Onboard with Floatra** on the Customer
    (``api.action_onboard_customer``).
    """
    if getattr(doc, "floatra_merchant_id", None):
        return  # already linked (e.g. an import)

    from ..api import onboard_customer

    try:
        onboard_customer(doc)
    except (ContractError, FloatraAPIError, ValueError) as err:
        _logger.warning("Floatra onboard failed for Customer %s: %s", doc.name, err)
