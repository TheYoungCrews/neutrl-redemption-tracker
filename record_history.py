#!/usr/bin/env python3
"""Append this run's headline numbers to data/history.jsonl and print current vs previous + deltas as JSON.
Run after compute.py (refresh.sh). Usage: .venv/bin/python record_history.py [--dry-run] [--history PATH]
Default PATH data/history.jsonl = the DAILY log kept by the local morning routine (not committed to git);
the hourly GitHub Actions run uses --history data/history_hourly.jsonl (committed on the tracker branch)."""
import json, sys, os
from datetime import datetime, timezone
HERE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(HERE, "data", "history.jsonl")
if "--history" in sys.argv:   # e.g. --history data/history_hourly.jsonl (used by the hourly GitHub Actions run)
    HIST = os.path.abspath(sys.argv[sys.argv.index("--history") + 1])
BLACKLISTED = "0xb0bfdc336537730ad2c44083c0f7c545aac79a9a"
REDEEM_TOPIC = "0xf3a670cd3af7d64b488926880889d08a8585a138ff455227af6737339a1ec262"
BIG_NUSD = 250_000  # flag single redemptions at or above this much NUSD

d = json.load(open(os.path.join(HERE, "data", "dashboard_data.json")))
t, b, r = d["totals"], d["blocks"], d["rates"]
now = d.get("supply", {}).get("now", {})
def summ(s):
    return {k: s[k] for k in ("wallets", "redeemed_wallets", "pct_wallets", "affected_usd",
                              "redeemed_usd_capped", "pct_capital", "usdc_paid")}
bl = next((w for w in d.get("top_wallets", []) if w["address"].lower() == BLACKLISTED), None)
rec = {
    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "generated_at_utc": d.get("generated_at_utc"),
    "as_of_block": b["as_of"], "as_of_ts": b["as_of_ts"], "last_redeem_block": b.get("last_redeem"),
    "redemption_rate": r["redemption_rate"], "snusd_share_rate": r.get("snusd_share_rate_now"),
    "snusd": summ(d["summary_snusd"]), "all": summ(d["summary_all"]),
    "usdc_paid_total": t["usdc_paid"], "nusd_redeemed_total": t["nusd_redeemed"],
    "redeem_events": t["redeem_events"], "unique_redeemers": t["unique_redeemers"],
    "usdc_funded_to_reserve": t["usdc_funded_to_reserve"], "usdc_reserve_now": t["usdc_reserve_now"],
    "supply": {k: now.get(k) for k in ("nusd_supply", "snusd_supply", "snusd_vault_nusd",
                                       "assetlock_nusd_balance", "assetlock_snusd_balance", "silo_nusd_balance")},
    "blacklisted_wallet": None if bl is None else {k: bl.get(k) for k in
        ("nusd", "snusd", "lock_nusd", "lock_snusd", "cooldown", "redeemed_nusd", "usdc", "redeemed", "remaining_value")},
    "strata": None,
}
st = d.get("strata")
if st:
    sr, jr, sy = st.get("senior") or {}, st.get("junior") or {}, st.get("strategy") or {}
    rec["strata"] = {
        "wipe_block": st.get("wipe_block"),
        "junior_wiped": jr.get("wiped"),
        "deposits_enabled_sr": (st.get("deposits_enabled") or {}).get("srNUSD"),
        "deposits_enabled_jr": (st.get("deposits_enabled") or {}).get("jrNUSD"),
        "sr_supply": (sr.get("now") or {}).get("supply"),
        "sr_assets_nusd": (sr.get("now") or {}).get("assets_nusd"),
        "sr_rate": (sr.get("now") or {}).get("exchange_rate_nusd"),
        "sr_withdraw_events_since_wipe": (sr.get("withdrawals_since_wipe") or {}).get("events"),
        "sr_withdraw_shares_since_wipe": (sr.get("withdrawals_since_wipe") or {}).get("shares"),
        "sr_withdraw_owners_since_wipe": (sr.get("withdrawals_since_wipe") or {}).get("unique_owners"),
        "jr_supply": (jr.get("now") or {}).get("supply"),
        "jr_assets_nusd": (jr.get("now") or {}).get("assets_nusd"),
        "jr_rate": (jr.get("now") or {}).get("exchange_rate_nusd"),
        "strategy_snusd": sy.get("snusd_balance_now"),
        "strategy_nusd_value": sy.get("nusd_value_now"),
        "senior_coverage": sy.get("senior_coverage_now"),
        "usdc_paid_via_strata": st.get("usdc_paid_via_strata"),
    }
prev = None
if os.path.exists(HIST):
    lines = [l for l in open(HIST) if l.strip()]
    if lines: prev = json.loads(lines[-1])

def diff(a, c):
    if isinstance(c, dict):
        a = a if isinstance(a, dict) else {}
        return {k: diff(a.get(k), v) for k, v in c.items() if isinstance(v, (int, float, dict)) and not isinstance(v, bool)}
    if isinstance(c, (int, float)) and isinstance(a, (int, float)): return c - a
    return None
deltas = None
if prev:
    deltas = {k: diff(prev.get(k), rec[k]) for k in ("snusd", "all", "usdc_paid_total", "nusd_redeemed_total",
              "redeem_events", "unique_redeemers", "usdc_funded_to_reserve", "usdc_reserve_now",
              "redemption_rate", "snusd_share_rate", "supply", "strata")}

since = prev["as_of_block"] if prev else b["as_of"]
big, n_new = [], 0
for lg in json.load(open(os.path.join(HERE, "data", "logs_redemption.json")))["logs"]:
    if lg["topics"][0].lower() != REDEEM_TOPIC: continue
    blk = int(lg["blockNumber"], 16)
    if blk <= since: continue
    n_new += 1
    raw = lg["data"][2:]
    nusd, usdc = int(raw[:64], 16) / 1e18, int(raw[64:128], 16) / 1e6
    if nusd >= BIG_NUSD:
        big.append({"redeemer": "0x" + lg["topics"][1][-40:], "nusd": nusd, "usdc": usdc, "block": blk, "tx": lg["transactionHash"]})

out = {"current": rec, "previous": prev, "deltas": deltas, "redemptions_since_previous": n_new,
       "big_redemptions_since_previous": big,
       "blacklisted_wallet_changed": bool(prev and prev.get("blacklisted_wallet") != rec["blacklisted_wallet"]),
       "rate_changed": bool(prev and prev.get("redemption_rate") != rec["redemption_rate"])}
if "--dry-run" not in sys.argv:
    if prev and prev["as_of_block"] == rec["as_of_block"]:
        out["note"] = "same as_of_block as previous entry; not appended (data did not advance)"
    else:
        with open(HIST, "a") as f: f.write(json.dumps(rec) + "\n")
print(json.dumps(out, indent=1))
