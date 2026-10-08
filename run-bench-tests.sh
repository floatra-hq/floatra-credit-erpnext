#!/usr/bin/env bash
# Local runner for the Frappe-runtime integration suite (`#57`).
#
# Assumes you already have a `frappe-bench` directory + a site that
# the `floatra_credit` app is installed on. The CI workflow at
# .github/workflows/erpnext-bench-tests.yml does the full setup
# from scratch; this script is for the inner-loop debug case.
#
# Usage:
#   ./run-bench-tests.sh                       # against ~/frappe-bench/test_site
#   BENCH_PATH=~/my-bench ./run-bench-tests.sh # custom bench
#   SITE=my-test ./run-bench-tests.sh          # custom site
#
# Prerequisites:
#   - Frappe + ERPNext installed on the named site
#   - This repo's connectors/erpnext/floatra_credit/floatra_credit
#     symlinked into <bench>/apps/floatra_credit
#   - `bench --site <site> install-app floatra_credit` already done
#
# The script clears Floatra-specific test rows before re-running so
# the unique-event_id assertions don't keep accumulating dupes
# across runs.

set -euo pipefail

BENCH_PATH="${BENCH_PATH:-$HOME/frappe-bench}"
SITE="${SITE:-test_site}"

if [ ! -d "$BENCH_PATH" ]; then
  echo "❌ Bench not found at $BENCH_PATH. Set BENCH_PATH or follow"
  echo "   the README to create one."
  exit 2
fi

cd "$BENCH_PATH"

echo "→ Clearing prior test rows on $SITE…"
bench --site "$SITE" execute floatra_credit.tests.integration._cleanup.clear || true

echo "→ Running floatra_credit integration tests…"
bench --site "$SITE" run-tests --app floatra_credit --module floatra_credit.tests.integration
