# -*- coding: utf-8 -*-
"""Scheduler tasks. Wired into hooks.py::scheduler_events.

  sync_credit_limits         every 4 hours
    Refreshes credit status, tier, limit and KYC for every enrolled
    Customer from ``GET /v1/partner/merchants/:id/credit-status``.

  poll_undelivered_webhooks  daily 03:30 UTC
    Pages ``GET /v1/partner/webhooks/undelivered``, replays each event
    through the live receiver's ``process_event`` (deduplicated on the event
    id), then acknowledges it so Floatra stops listing it. A failed replay
    is not acknowledged and is listed again next run.

(The 30-minute approval-expiry notifier was removed in 0.3.0: Floatra
retired the 2-hour approval window.)
"""

import logging

import frappe

from .services.floatra_api import FloatraAPIClient, FloatraAPIError

_logger = logging.getLogger(__name__)

MAX_PER_CYCLE = 200
PAGE_SIZE = 50


def sync_credit_limits() -> None:
    """Best-effort: one Customer's failure is logged and the sync continues."""
    from .api import sync_customer

    customers = frappe.get_all(
        "Customer",
        filters={"floatra_merchant_id": ["is", "set"]},
        fields=["name", "floatra_merchant_id"],
    )
    if not customers:
        return
    try:
        client = FloatraAPIClient.for_site()
    except FloatraAPIError as err:
        _logger.warning("Floatra not configured; skipping sync: %s", err)
        return
    for row in customers:
        try:
            sync_customer(client, row.name, row.floatra_merchant_id)
        except (FloatraAPIError, ValueError) as err:
            _logger.warning("Credit status sync failed for %s: %s", row.name, err)
    frappe.db.commit()


def poll_undelivered_webhooks() -> int:
    """Replay undelivered events; returns how many were applied."""
    from .controllers.webhook import process_event

    try:
        client = FloatraAPIClient.for_site()
    except FloatraAPIError as err:
        _logger.warning("Undelivered-webhook poll skipped (config error): %s", err)
        return 0

    replayed = 0
    scanned = 0
    offset = 0
    while scanned < MAX_PER_CYCLE:
        try:
            page = client.get_undelivered_webhooks(limit=PAGE_SIZE, offset=offset)
        except (FloatraAPIError, ValueError) as err:
            _logger.warning("Undelivered-webhook poll stopped at offset %d: %s", offset, err)
            break
        if not page["events"]:
            break
        # Acknowledged events leave Floatra's list: the next page starts
        # after the ones still listed (the failures).
        still_listed = 0
        for event in page["events"]:
            scanned += 1
            outcome = process_event(event["event_id"], event["payload"])
            if outcome == "failed":
                # Not applied: leave it listed so the next run retries it.
                still_listed += 1
                continue
            if outcome in ("ok", "ignored"):
                replayed += 1
            # Acknowledge only what is settled here: applied now, already
            # processed before (duplicate) or deliberately ignored.
            _try_ack(client, event["event_id"])
        if not page["has_more"]:
            break
        offset += still_listed

    _logger.info("Undelivered-webhook poll: scanned %d, replayed %d", scanned, replayed)
    return replayed


def _try_ack(client: FloatraAPIClient, event_id: str) -> None:
    """Acknowledge; a 404 means it was already acknowledged."""
    try:
        client.acknowledge_webhook(event_id)
    except FloatraAPIError as err:
        if err.status != 404:
            _logger.warning("ACK failed for %s: %s", event_id, err)
