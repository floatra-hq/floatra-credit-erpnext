# Changelog

All notable changes to the Floatra app for ERPNext (`floatra_credit`).
Versions follow `floatra_credit/__init__.py` `__version__`.

## [0.3.0]

First public release, for Frappe / ERPNext v15. MIT licence.

- **Matches Floatra's partner API end to end** (#1302). Real `/v1/partner/...`
  routes, Floatra's response envelope and `errorCode`, decimal-Naira money, and
  working onboarding with no BVN/NIN. The Floatra Loan id drives lifecycle calls
  and reconciliation. Webhooks verify the Unix-seconds signature, are anchored
  to the platform and realm (`livemode`) and read flat payloads. KYC goes
  through Floatra's hosted link. Adds **Onboard with Floatra** and a
  per-Customer sync. The `v0_3_0_partner_contract` patch adds
  `floatra_loan_id` / `floatra_interest_amount`, removes retired fields
  (blanking stored BVN/NIN) and migrates the gateway URL. Contract tests run
  against fixtures rendered by Floatra core.
- **Bench suite passes under `bench run-tests`** (#1313): 97 tests on a fresh
  ERPNext v15 site; the plain suite (131 tests) runs without a bench.
- **One "Floatra" workspace** (#1313). The workspace is named "Floatra"
  throughout, so re-running `after_install` no longer inserts a duplicate.
- **Installs with `bench get-app`.** `requirements.txt` no longer lists
  `frappe` (bench provides it, as Frappe's own app template says); with it,
  bench 5.x's uv-based install tried to re-resolve Frappe from PyPI and failed.
- **The financed amount is the tax-inclusive order total** (founder ruling
  2026-10-08): the loan covers the full invoice (`grand_total`), VAT included.
