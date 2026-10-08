# -*- coding: utf-8 -*-
"""Shared plumbing for the plain-Python contract tests.

``EXAMPLES`` are the inputs behind this app's request fixtures
(``fixtures/contract/request-*.json``), which Floatra core validates against
its DTOs. Regenerate them from ``connectors/erpnext/floatra_credit`` with
``python3 -m floatra_credit.tests.contract_support``.
"""

import json
import os
from datetime import datetime
from types import SimpleNamespace as NS

from ..services import floatra_api, floatra_contract

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "contract")


def fixture(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


def fixture_names(prefix):
    return sorted(
        f[: -len(".json")]
        for f in os.listdir(FIXTURES)
        if f.startswith(prefix) and f.endswith(".json")
    )


# ----- Example documents (ERPNext-shaped stand-ins) -----

CUSTOMER = NS(
    name="CUST-77",
    customer_name="Mama Nkechi Stores",
    mobile_no="+234 803 123 4567",
    email_id="nkechi@example.com",
    floatra_business_type="RETAILER",
    floatra_primary_category="FMCG",
    floatra_assigned_agent_id="agt_01",
    floatra_owner_first_name="Nkechi",
    floatra_owner_last_name="Okafor",
)
ADDRESS = {"address_line1": "12 Market Road", "city": "Ikeja", "state": "Lagos"}

SALES_ORDER = NS(
    name="SAL-ORD-2026-00042",
    customer="CUST-77",
    grand_total=250000.0,
    floatra_tenure_days="14",
    floatra_agent_name="Tunde Bello",
    floatra_confirmation_method="ERP_CONSOLE",
    floatra_delivery_lat=6.6018,
    floatra_delivery_lng=3.3515,
    floatra_delivery_accuracy_meters=12,
    floatra_delivery_location_at="2026-10-01 12:29:00",  # desk time, Lagos
    floatra_proof_of_delivery_url="https://erp.example.com/pod/00042.jpg",
)

DELIVERED_AT = datetime(2026, 10, 1, 11, 30)


def build_requests():
    """Every request body this app sends, from the examples above."""
    c = floatra_contract
    initiate = floatra_api.initiate_body_for_sales_order(SALES_ORDER, "FMCG")
    return {
        "request-onboard": floatra_api.onboard_body_for_customer(CUSTOMER, ADDRESS),
        "request-initiate": initiate,
        "request-confirm-delivery": floatra_api.confirm_delivery_body_for_sales_order(
            SALES_ORDER, DELIVERED_AT, notes="Delivered to the shop",
            tz_name="Africa/Lagos",
        ),
        "request-cancel": c.cancel_body(
            reason_code="OTHER",
            reason_notes="Sales Order SAL-ORD-2026-00042 cancelled in ERPNext.",
        ),
        "request-repayment": c.repayment_body(
            amount="100000",
            reference="RCPT-0042",
            source="BANK_TRANSFER",
            collected_at="2026-10-01 11:00:00",
            notes="Transfer ref 991",
        ),
        "request-return": c.return_body(
            return_amount="50000.00",
            reason_code="DAMAGED",
            reason_notes="Crushed cartons",
        ),
        "request-bank-account": c.bank_account_body(
            bank_code="058",
            account_number="0123456789",
            account_name="Nkechi Okafor",
            is_primary=True,
        ),
        "request-bulk": c.bulk_body([initiate], "erp-backfill-2026-10-01"),
    }


def write_request_fixtures():
    for name, body in build_requests().items():
        with open(os.path.join(FIXTURES, name + ".json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(body, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    write_request_fixtures()
