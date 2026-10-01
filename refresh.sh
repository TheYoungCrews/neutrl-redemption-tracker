#!/usr/bin/env bash
# Neutrl redemption dashboard - LOCAL refresh.
# Since Oct 1, 2026 the public site is refreshed HOURLY by GitHub Actions (.github/workflows/refresh.yml on the
# main branch of TheYoungCrews/neutrl-redemption-tracker). Code + incremental data live on the `tracker` branch,
# and this directory is a git checkout of that branch. The repo is the source of truth for data.
#
# Usage:
#   ./refresh.sh                  sync code + data from GitHub (tracker branch), rebuild site/ locally. Pushes nothing.
#   ./refresh.sh --pages          same, then publish_pages.sh, which pushes ONLY if the local data is newer than
#                                 the live site (normally a no-op because Actions is at most ~1h behind).
#   ./refresh.sh --local [--pages]  FALLBACK if Actions is failing: pull logs + compute locally, rebuild (+ publish).
#                                 Local data changes are discarded again by the next plain sync.
set -euo pipefail
cd "$(dirname "$0")"
LOCAL=0; PAGES=0; DEPLOY=0
for a in "$@"; do case "$a" in --local) LOCAL=1;; --pages) PAGES=1;; --deploy) DEPLOY=1;; *) echo "unknown option $a" >&2; exit 2;; esac; done
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }

# 1) sync from GitHub: tracked data/ is owned by the hourly Actions run -> drop local copies, then fast-forward
git checkout -q -- data/ 2>/dev/null || true
git pull -q --rebase --autostash origin tracker
.venv/bin/python logstore.py unpack >/dev/null      # rebuild data/logs_*.json from data/segments/

# 2) optional local recompute (fallback only)
if [ "$LOCAL" = 1 ]; then
  .venv/bin/python pull_logs.py      # incremental eth_getLogs (keyless public RPC)
  .venv/bin/python compute.py > data/last_compute.log
fi

# 3) rebuild site/ (pre-rendered with headless Chrome, og.png, favicon)
.venv/bin/python build.py

# freshness warning: Actions should keep the data < ~2h old
.venv/bin/python - <<'PY'
import json, time
b = json.load(open("data/dashboard_data.json"))["blocks"]; age = (time.time() - b["as_of_ts"]) / 3600
print(f"data as of block {b['as_of']}, {age:.1f}h old")
if age > 3: print("WARNING: data is more than 3h old - the hourly GitHub Actions run may be failing. "
                  "Check https://github.com/TheYoungCrews/neutrl-redemption-tracker/actions ; fallback: ./refresh.sh --local --pages")
PY

if [ "$DEPLOY" = 1 ]; then ./deploy.sh; fi
if [ "$PAGES" = 1 ]; then ./publish_pages.sh; fi
