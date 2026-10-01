# Neutrl Redemption Tracker (NUSD / sNUSD)

Static, single-page tracker, refreshed hourly, showing how much NUSD/sNUSD has been redeemed through Neutrl's
0.51-USDC redemption contract, compared with positions held at the freeze snapshot.

## Hourly auto-refresh (GitHub Actions) - since Oct 1, 2026
The live site https://theyoungcrews.github.io/neutrl-redemption-tracker/ is rebuilt every hour at :17 by
`.github/workflows/refresh.yml` on the **main** branch of TheYoungCrews/neutrl-redemption-tracker (public repo,
free Actions minutes, default GITHUB_TOKEN, no secrets).
- **main** = what GitHub Pages serves (index.html, og.png, favicon.svg, apple-touch-icon.png, .nojekyll) + the workflow.
- **tracker** = pipeline code + incremental data state. THIS directory is a git checkout of `tracker`
  (`.gitignore` is a whitelist; secrets, .venv, .tools, logs, site/ are never committed).
  Raw logs are stored as append-only 100k-block segments in `data/segments/` (`logstore.py pack|unpack`
  converts to/from the `data/logs_*.json` files pull_logs.py/compute.py use).
- Each run: unpack -> pull_logs.py -> compute.py -> build.py -> `ci_check.py` (sanity checks vs the last commit:
  as_of block, totals and log counts never go backwards, page markers, og.png size) -> `record_history.py --history
  data/history_hourly.jsonl` -> pack -> commit data to `tracker` -> copy the site to `main`. Any failure (e.g. RPC
  errors) stops the run before committing, so the last good page stays live. Runs never overlap (concurrency group).
- Manual run: `gh workflow run refresh.yml -R TheYoungCrews/neutrl-redemption-tracker` (or the Actions tab).
- Code/template changes: edit here, `git commit` and `git push origin tracker`; the next hourly run (or a manual
  run) publishes them. `./publish_pages.sh --force` ships a local build immediately.
- Hero deltas: change vs the hourly snapshot ~24h earlier (`data/history_hourly.jsonl`); before 24h of hourly
  history exists, local builds fall back to the newest older entry in the local-only `data/history.jsonl`
  (not in git, so Actions builds simply hide the deltas until 24h of hourly history exists).

## Refresh (local)
    ./refresh.sh            # sync code+data from GitHub (tracker branch) and rebuild site/ locally; pushes nothing
    ./refresh.sh --pages    # + publish_pages.sh, which pulls first and pushes ONLY if local data is newer than live
    ./refresh.sh --local --pages   # FALLBACK if Actions is failing: pull logs + compute locally, rebuild, publish

## Files
- `rpc.py`              keyless JSON-RPC helper (Tenderly public gateway for wide eth_getLogs ranges; drpc/publicnode fallback)
- `pull_logs.py`        incremental raw log pull -> `data/logs_*.json` (NUSD, sNUSD, NusdRedemption, AssetReserve, AssetLock, USDC flows to/from reserve)
- `label_contracts.py`  one-off: EOA / EIP-7702 / contract detection + Blockscout names for snapshot holders -> `data/address_meta.json` (compute.py labels new addresses on the fly)
- `compute.py`          all metrics -> `data/dashboard_data.json`
- `build.py` + `template.html` -> `site/index.html` (data embedded, Chart.js from jsDelivr, pre-rendered with headless Chrome),
  `site/og.png` (1200x630 share card), favicon. Config at the top of build.py (SITE_URL, close date, analytics token)
- `logstore.py`         log state <-> git-friendly segments; `ci_check.py` pre-publish sanity checks (Actions)
- `publish_pages.sh`    manual/local publish of site/ to the `main` branch (Actions normally does this)
- `refresh.sh`          local sync/rebuild wrapper (see above)

## Key onchain facts (Ethereum mainnet)
- NUSD 0xE556ABa6fe6036275Ec1f87eda296BE72C811BCE; sNUSD 0x08EFCC2F3e61185D0EA7F8830B3FEc9Bfa2EE313 (Silo 0x6cdFC009AB1c5f8114A8aA0117A7E6FCbB35bb9B)
- NusdRedemption 0xB3f07D3392102fC23264a78e2A1A8B6421123828 (rate 0.51e18), USDC AssetReserve 0xfEE69fa9C94B9D967390a4F4144E2746603eC1Ed
- AssetLock 0x99161BA892ECae335616624c84FAA418F64FF9A6 (looked through); Strata sNUSDStrategy 0x3cef2c09c4fad37e9bdd86cd9810c3042fb5de88
- Freeze: sNUSD Paused() block 25,745,732 (2026-08-13 11:14:59 UTC). Snapshot = end of block 25,745,731.
- Reopen: sNUSD cooldown -> 1s block 25,997,006; NusdRedemption Unpaused block 25,997,438 (2026-09-17 13:14:23 UTC).

## Local daily summary (optional morning routine; separate from the hourly site refresh)
    ./refresh.sh --pages                 # sync latest data from GitHub (no push unless local is newer)
    .venv/bin/python record_history.py   # appends headline numbers to data/history.jsonl (DAILY, local-only, not in git)
                                         # and prints current vs previous daily entry, deltas, big redemptions,
                                         # blacklisted-wallet and rate changes
A daily 8:12 AM ET routine (until redemptions close; Neutrl: expected open until Nov 14, 2026) runs the two commands
above, checks the live GitHub Pages site, and sends a morning update. The hourly Actions run writes its own
snapshots to data/history_hourly.jsonl, so the daily "previous" entry is unaffected.
