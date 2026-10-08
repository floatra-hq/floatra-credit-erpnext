# Floatra for ERPNext

Native [Frappe](https://frappeframework.com/) app that embeds Floatra inventory financing into the ERPNext sales workflow. **Mode A** integration — implements **Prompt 4** of the ERP Integration Spec.

Functionally equivalent to the Odoo connector in `connectors/odoo/floatra_credit/`, built for the Frappe/ERPNext framework instead.

---

## Install

Releases are published to the read-only mirror
[`floatra-hq/floatra-credit-erpnext`](https://github.com/floatra-hq/floatra-credit-erpnext)
(tag `v<version>`, see its Releases page and `CHANGELOG.md`). Pin a release
tag rather than tracking `main`:

```bash
# From your ERPNext bench directory (Frappe / ERPNext v15):
bench get-app https://github.com/floatra-hq/floatra-credit-erpnext --branch v0.3.1
bench --site <your-site> install-app floatra_credit
bench --site <your-site> migrate   # runs the field patches
```

To upgrade, fetch the new tag into `apps/floatra_credit` (or `bench get-app`
it again with `--overwrite`) and run `bench --site <your-site> migrate`.

Each GitHub Release also carries `floatra_credit-erpnext-<version>.zip` (the
app directory, for offline installs: unzip into `<bench>/apps/`, then
`bench setup requirements` and `install-app`) and its `.sha256`. Verify the
download before you unzip it:

```bash
shasum -a 256 -c floatra_credit-erpnext-0.3.1.zip.sha256   # macOS
sha256sum -c floatra_credit-erpnext-0.3.1.zip.sha256       # Linux
```

Only install from `github.com/floatra-hq`. Anything published under another
owner is not from Floatra.

Then configure under **Floatra → Configuration** (System Manager role):

- **Gateway URL** — `https://api.floatra.com` (host only; the app adds `/v1/partner/...`). The same host serves live and sandbox: the API key decides which. A stored `…/v1` or the old `api.floatra.io` default is migrated by the 0.3.0 patch.
- **Platform ID** — issued by Floatra
- **API Key** — issued by Floatra (stored encrypted via Frappe's `Password` field type)
- **Webhook Secret** — issued by Floatra (used for inbound HMAC verification)
- **Enforcement Mode** — `REORDER_LOCK` (recommended for ERPNext deployments)

The webhook receiver lives at:

```
POST /api/method/floatra_credit.controllers.webhook.receive
```

Hand that URL to Floatra ops so they can register it on your platform record.

---

## What the app does

### Status

Every call the app makes and every webhook it handles is checked against
Floatra core's real contract (0.3.0): core renders the request/response
pairs and signed webhook deliveries from its own controllers, DTO
validation, exception filter and webhook pipeline into
`floatra_credit/tests/fixtures/contract/`, and core validates this app's
request bodies against its DTOs. The plain-Python suite runs the client,
parsers, webhook application and desk endpoints (frappe stubbed) against
those files.

Known limitations:

- The bench suite (`floatra_credit/tests/integration/`) needs a Frappe +
  ERPNext site; run it (`./run-bench-tests.sh`) before the first
  distributor install.
- **Initiate Return** is refused by Floatra (`RETURN_015`) unless the loan
  has an open delivery dispute, which online orders cannot have yet; the
  refusal is shown as a plain message.
- `platform.suspended` is logged only; Floatra sends no "reinstated" event.
- Requesting credit again on a declined order returns the same decision
  (Floatra deduplicates on the order reference); use a new Sales Order.
- Request Financing needs the Customer `available` (KYC done) with a credit
  status synced in the last 15 minutes (**Sync from Floatra**, the 4-hourly
  sync or a Floatra webhook).

### Custom fields (added via patches — survive ERPNext upgrades)

- **Customer**: `floatra_merchant_id`, `floatra_credit_status`, `floatra_credit_limit`, `floatra_credit_tier`, `floatra_kyc_status`, `floatra_kyc_url`, `floatra_kyc_url_expires_at`, `floatra_last_status_check`, plus the optional onboarding profile (business type, category, assigned agent, owner name)
- **Sales Order**: `floatra_order_id` (Floatra's Order), `floatra_loan_id` (Floatra's Loan: every lifecycle call and loan webhook uses it), `floatra_decision`, `floatra_decision_reason`, `floatra_approved_amount`, `floatra_interest_amount`, `floatra_repayment_amount`, `floatra_repayment_due_date`, `floatra_disbursed` (funded), `floatra_delivery_confirmed`, `floatra_tenure_days`, delivery-context fields
- **Item Group**: `floatra_category` (maps to Floatra's `OrderCategory` enum)

### Workflow

1. **Customer created** → `controllers/customer.after_insert` best-effort calls `POST /v1/partner/merchants/onboard` (Customer name = `external_merchant_id`; a Nigerian mobile number is required; no BVN/NIN is sent) and stores the Floatra merchant id. On failure, **Floatra → Onboard with Floatra** retries (`api.action_onboard_customer`).
2. **Floatra → Get KYC Link** → hosted KYC link (below).
3. **Request Financing** on a draft Sales Order → `api.request_credit` → `POST /v1/partner/orders/initiate` (decimal-Naira `amount`, item-group category by revenue). The decision, Order id and Loan id land on the Sales Order.
4. **Sales Order submit** → `before_submit` refuses while credit is pending / approved / waitlisted and not yet funded.
5. **Floatra webhook** → `controllers/webhook.receive` verifies the signature (Unix-seconds `X-Floatra-Timestamp`, HMAC over `"<ts>.<raw body>"`, 5-minute window; 401 otherwise), deduplicates on `X-Floatra-Event-ID`, applies the flat payload. `order.funded` / `order.disbursed` set the funded flag; every event is logged (PII-redacted) in `Floatra Webhook Log`.
6. **Confirm Delivery / Record Cash Repayment / Initiate Return / Cancel** → the `/v1/partner/orders/:loan_id/...` routes.
7. **Scheduler**: every 4h `tasks.sync_credit_limits`; daily `tasks.poll_undelivered_webhooks` (replay + acknowledge).

---

## Architecture

```
floatra_credit/
├── hooks.py                         doc_events, scheduler, patches wiring
├── modules.txt                      "Floatra Credit"
├── api.py                           Whitelisted desk endpoints
├── tasks.py                         Scheduled: sync, undelivered-webhook replay
├── floatra_credit/doctype/
│   ├── floatra_platform_config/     Singleton — gateway URL, secrets
│   └── floatra_webhook_log/         Event ledger (event_id is unique key)
├── patches/                         Field installation (idempotent)
│   ├── add_floatra_fields_to_customer.py
│   ├── add_floatra_fields_to_sales_order.py
│   └── add_floatra_category_to_item_group.py
├── controllers/
│   ├── sales_order.py               before_submit gate
│   ├── customer.py                  after_insert onboard
│   └── webhook.py                   HMAC + idempotency + dispatch
├── services/
│   ├── floatra_contract.py          Wire contract: URLs, money, bodies, parsers, webhooks (shared with Odoo)
│   ├── floatra_client.py            HTTP client for /v1/partner/* (shared with Odoo)
│   ├── floatra_api.py               Settings → client; ERPNext docs → request bodies
│   └── credit_lock.py               15-min staleness failsafe
├── public/js/
│   ├── sales_order.js               "Request Financing" button + badge
│   └── customer.js                  "Sync from Floatra" action
└── tests/
    ├── test_contract.py             Contract fixtures: bodies, client, parsers, webhooks, desk endpoints
    └── integration/                 Bench suite (needs a site)
```

---

## Identity verification (KYC): hosted link

Production KYC is a selfie and liveness check on the merchant's own phone,
so the app never sends a BVN, NIN or photo. On an onboarded Customer,
**Floatra → Get KYC Link** calls `POST /v1/partner/merchants/:id/kyc/retry`
with an empty body (`api.action_get_kyc_link`). Floatra returns a
single-use link valid for 24 hours; it is stored on the Customer
(`floatra_kyc_url`, `floatra_kyc_url_expires_at`) and shown in a dialog
with **Copy Link** and **Email to Customer** (Frappe's email composer,
reviewed and sent by the user). While the link is unexpired the Floatra
menu also offers **Copy KYC Link** and **Email KYC Link**. The merchant
confirms a code sent to their phone number on Floatra, enters their NIN or
BVN and takes the selfie; the result arrives as the
`merchant.kyc_completed` webhook, which also clears the stored link.
Getting a new link revokes the previous one; Floatra allows 5 links per
merchant and 200 per platform an hour.

Refusals are shown as plain messages: already verified (409
`KYC_ALREADY_VERIFIED`), not available (409 `NOT_AVAILABLE`), too many links
(429), KYC temporarily off (503), unknown merchant (404), another platform's
merchant (403), wrong environment's key (422).

**Access (accepted risk).** The stored `floatra_kyc_url` is a bearer link
(single use, and the merchant must still pass an OTP sent to the phone on
file). ERPNext has no Floatra-specific role, so anyone who can read the
Customer can see it, and `action_get_kyc_link` is gated by Customer write
permission only. Restrict Customer read access accordingly; the link is
cleared when the merchant completes KYC and dies after 24 hours.

With a **sandbox** key (any key that does not start `live_pk_`: `sbx_pk_…`,
or `pk_…` after a rotation) Floatra returns a simulated result
instead of a link; the app sends a fixed test body (dummy identifier, 1x1
placeholder image) and mirrors the result onto `floatra_kyc_status`.

---

## Security model

| Surface | Defence |
|---|---|
| Inbound webhooks | HMAC-SHA256 over `"<timestamp>.<raw body>"`, `hmac.compare_digest` constant-time; Unix-seconds timestamp within 5 minutes |
| Webhook replay | `event_id` is a unique key on `Floatra Webhook Log`; duplicates return 200/duplicate |
| API key / webhook secret | Stored as Frappe `Password` fields → encrypted in the DB |
| Sales Order submit | `before_submit` hook blocks submission when credit was requested but disbursement is pending |
| Stale credit status | `credit_lock.is_locked_failsafe` treats >15 min cache as locked |
| Field installation | Custom-field patches (not fixture JSON) survive ERPNext upgrades |

---

## Testing

The unit tests need no bench (`frappe` is stubbed; Python 3.10+ with
`requests`; pytest also works), from `connectors/erpnext/floatra_credit`:

```bash
python3 -m unittest \
    floatra_credit.tests.test_contract \
    floatra_credit.tests.test_kyc_link \
    floatra_credit.tests.test_api_authorization \
    floatra_credit.tests.test_floatra_api_security \
    floatra_credit.tests.test_credit_lock \
    floatra_credit.tests.test_delivery_validation \
    floatra_credit.tests.test_pii_redaction -v
```

`floatra_credit/tests/fixtures/contract/` holds the responses and signed
webhook deliveries rendered by Floatra core's contract test
(`src/modules/partner-webhooks/partner-contract-fixtures.spec.ts`;
regenerate there with `UPDATE_CONTRACT_FIXTURES=1`). The `request-*` files
are this app's request bodies (regenerate with
`python3 -m floatra_credit.tests.contract_support`), which core validates
against its DTOs.

The Frappe-runtime integration suite (DocType installability,
custom-field patches, `before_submit` gate, webhook idempotency,
category aggregation) lives in `floatra_credit/tests/integration/`
and runs inside a real Frappe + ERPNext bench:

```bash
# Local — assumes you already have a bench with floatra_credit installed
./run-bench-tests.sh
# or
bench --site <your-site> run-tests --app floatra_credit \
  --module floatra_credit.tests.integration
```

For CI, see `.github/workflows/erpnext-bench-tests.yml` — spins up
MariaDB + Redis services, runs `bench init`, installs ERPNext +
the app, and runs the integration suite. Triggered on PRs that
touch `connectors/erpnext/floatra_credit/**`.

---

## Changelog

- **0.3.0 (2026-10-07):** Every call and webhook matches Floatra's current
  partner API, contract-tested against core-rendered fixtures. Gateway URL
  is host-only (`https://api.floatra.com`; migrated by the
  `v0_3_0_partner_contract` patch). All calls use `/v1/partner/...`, unwrap
  Floatra's `{success, data, …}` envelope, map errors by `errorCode`, and
  send money as decimal-Naira strings (no `amount_kobo`, no `is_test`;
  Initiate Return now takes Naira). Onboarding hits the real route and
  stores the merchant id; it no longer sends BVN/NIN, and the BVN/NIN
  Customer fields are blanked and removed. New Sales Order fields
  `floatra_loan_id` and `floatra_interest_amount`; lifecycle calls and
  reconciliation use the Loan id (looked up by order reference for older
  orders). Webhooks verify the Unix-seconds timestamp signature (401 on
  failure), read core's flat camelCase payloads, and `order.funded` now sets
  the funded flag like `order.disbursed`. New desk actions **Onboard with
  Floatra** and a per-Customer **Sync from Floatra** (the old button called
  a non-whitelisted task). The cancel hook sends a valid cancel body.
  Removed: test mode, the approval-expiry notifier (Floatra retired the
  2-hour window), `floatra_is_test` / `floatra_approval_expires_at`. Fixed a
  syntax error that stopped the Sales Order client script loading. Run
  `bench --site <site> migrate` after updating.
  Webhook safety: events whose `livemode` does not match the configured
  key are logged and ignored (a live key also ignores unmarked events);
  records are matched by Floatra loan / loan-request / merchant ids first,
  and an ERP-local order or customer name is accepted only when its stored
  Floatra ids agree. Only successfully applied deliveries are deduplicated:
  the log row is committed first, a failed apply is rolled back (the row
  is marked failed) and retried on Floatra's next delivery or the replay
  task; a signature already applied is not applied again under a new
  event id.
  New **Cancel Floatra Credit** on approved, unfunded drafts and an
  `on_trash` hook release Floatra's reservation (a draft is deleted, never
  cancelled). Desk date-times (delivered at, collected at, location time)
  are converted from the site timezone to UTC. Reconciliation counts
  `FUNDED` loans as funded. The patch also strips BVN/NIN entries from the
  Customer's Version history and adds `floatra_loan_request_id`. Onboarding
  sends `owner.email` only when it is a single valid address; the
  bank-account Idempotency-Key no longer carries the account number.
- **0.2.0 (2026-10-07):** KYC uses Floatra's hosted link. "Retry KYC"
  (which sent the Customer's BVN/NIN to a drifted path; production refuses
  identity data with `400 KYC_HOSTED_LINK_ONLY`) is replaced by **Get KYC
  Link** (`api.action_get_kyc_link`, replacing `api.action_retry_kyc`) plus
  Copy / Email actions. It no longer needs a BVN/NIN on the Customer. New
  Customer fields via the `add_kyc_link_fields_to_customer` patch — run
  `bench --site <site> migrate` after updating. `merchant.kyc_completed`
  clears the stored link.

---


> **Update 2026-05-12 (c):** Delivery-confirmation desk dialog is now wired (closes #48). The Sales Order's "Confirm Delivery" button now opens an inline `frappe.ui.Dialog` (datetime + notes) that submits to a new whitelisted `confirm_delivery_for_sales_order` method. Validation logic in `services/delivery_validation.py` (13 unit tests): rejects future-dated deliveries beyond a 5-min clock-skew tolerance, rejects deliveries older than 30 days, caps `notes` at 500 chars. Mirrors the gateway-side `floatra_delivery_confirmed` flag on the SO immediately for a snappy desk experience.
