# -*- coding: utf-8 -*-
"""Unit tests for services.delivery_validation.

Pure-Python — covers the validator the confirm-delivery dialog
posts through `api.confirm_delivery_for_sales_order`.
"""

import unittest
from datetime import datetime, timedelta, timezone

from ..services.delivery_validation import (
    DeliveryValidationError,
    validate_delivery_inputs,
)


NOW = datetime(2026, 5, 12, 12, 0, 0)  # naive UTC


class TestValidateDeliveryInputs(unittest.TestCase):
    def test_accepts_now_iso_with_offset(self):
        out = validate_delivery_inputs(
            delivered_at="2026-05-12T11:00:00+00:00",
            now=NOW,
        )
        self.assertTrue(out["delivered_at"].startswith("2026-05-12T11:00:00"))
        self.assertNotIn("notes", out)

    def test_accepts_frappe_desk_format(self):
        out = validate_delivery_inputs(
            delivered_at="2026-05-12 11:30:00",
            now=NOW,
        )
        self.assertTrue(out["delivered_at"].startswith("2026-05-12T11:30:00"))

    def test_accepts_z_suffix(self):
        out = validate_delivery_inputs(
            delivered_at="2026-05-12T11:00:00Z",
            now=NOW,
        )
        self.assertTrue(out["delivered_at"].startswith("2026-05-12T11:00:00"))

    def test_accepts_python_datetime(self):
        out = validate_delivery_inputs(
            delivered_at=NOW - timedelta(minutes=5),
            now=NOW,
        )
        self.assertIn("delivered_at", out)

    def test_includes_notes_when_provided(self):
        out = validate_delivery_inputs(
            delivered_at=NOW - timedelta(minutes=1),
            notes="Delivered by Tunde, vehicle KJA-123",
            now=NOW,
        )
        self.assertEqual(out["notes"], "Delivered by Tunde, vehicle KJA-123")

    def test_strips_notes_whitespace(self):
        out = validate_delivery_inputs(
            delivered_at=NOW - timedelta(minutes=1),
            notes="   note   ",
            now=NOW,
        )
        self.assertEqual(out["notes"], "note")

    def test_empty_notes_omitted_from_payload(self):
        out = validate_delivery_inputs(
            delivered_at=NOW - timedelta(minutes=1),
            notes="   ",
            now=NOW,
        )
        self.assertNotIn("notes", out)

    # ---- failures ----

    def test_rejects_missing_delivered_at(self):
        with self.assertRaises(DeliveryValidationError) as ctx:
            validate_delivery_inputs(delivered_at=None, now=NOW)
        self.assertEqual(ctx.exception.field, "delivered_at")

    def test_rejects_unparseable_delivered_at(self):
        with self.assertRaises(DeliveryValidationError) as ctx:
            validate_delivery_inputs(
                delivered_at="yesterday",
                now=NOW,
            )
        self.assertEqual(ctx.exception.field, "delivered_at")

    def test_rejects_future_dated_delivery(self):
        # 1 hour in the future — way beyond the 5-min clock skew tolerance
        future = NOW + timedelta(hours=1)
        with self.assertRaises(DeliveryValidationError) as ctx:
            validate_delivery_inputs(delivered_at=future, now=NOW)
        self.assertEqual(ctx.exception.field, "delivered_at")
        self.assertIn("future", str(ctx.exception))

    def test_accepts_slightly_future_within_clock_skew(self):
        # 2 minutes in the future — within the 5-min tolerance
        slightly_future = NOW + timedelta(minutes=2)
        out = validate_delivery_inputs(
            delivered_at=slightly_future, now=NOW,
        )
        self.assertIn("delivered_at", out)

    def test_rejects_far_past_delivery(self):
        # 31 days back — beyond the 30-day max
        far_past = NOW - timedelta(days=31)
        with self.assertRaises(DeliveryValidationError) as ctx:
            validate_delivery_inputs(delivered_at=far_past, now=NOW)
        self.assertEqual(ctx.exception.field, "delivered_at")
        self.assertIn("past", str(ctx.exception))

    def test_rejects_over_long_notes(self):
        with self.assertRaises(DeliveryValidationError) as ctx:
            validate_delivery_inputs(
                delivered_at=NOW - timedelta(minutes=1),
                notes="x" * 501,
                now=NOW,
            )
        self.assertEqual(ctx.exception.field, "notes")


if __name__ == "__main__":
    unittest.main()
