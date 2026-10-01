"""Sanity checks run by the hourly GitHub Actions job BEFORE anything is committed or published.
Exit code 1 -> the workflow stops and the last good page stays live. Compares against the committed (HEAD) data."""
import json, os, re, struct, subprocess, sys
errs, warns = [], []
def head_json(path):
    try: return json.loads(subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True, check=True).stdout)
    except Exception: return None
cur = json.load(open("data/dashboard_data.json")); prev = head_json("data/dashboard_data.json")
b, t = cur["blocks"], cur["totals"]
if len(cur.get("all_wallets", [])) < 1000: errs.append(f"all_wallets has only {len(cur.get('all_wallets', []))} rows")
if not cur.get("daily"): errs.append("daily is empty")
for k in ("summary_all", "summary_snusd", "buckets_all", "buckets_snusd", "contracts", "outsiders", "restricted_list", "top_wallets"):
    if not cur.get(k): errs.append(f"{k} missing/empty")
if prev:
    pb, pt = prev["blocks"], prev["totals"]
    if b["as_of"] < pb["as_of"]: errs.append(f"as_of went backwards: {b['as_of']} < {pb['as_of']}")
    for k in ("redeem_events", "unique_redeemers"):
        if t[k] < pt[k]: errs.append(f"totals.{k} decreased {pt[k]} -> {t[k]}")
    for k in ("usdc_paid", "nusd_redeemed"):
        if t[k] < pt[k] - 1: errs.append(f"totals.{k} decreased {pt[k]} -> {t[k]}")
    pw, cw = prev["summary_all"]["wallets"], cur["summary_all"]["wallets"]
    if abs(cw - pw) > max(5, 0.02 * pw): errs.append(f"snapshot wallet count jumped {pw} -> {cw}")
# log state must only grow
for n in ("nusd", "snusd", "redemption", "assetreserve", "assetlock"):
    m = head_json(f"data/segments/{n}/meta.json")
    if m and os.path.exists(f"data/logs_{n}.json"):
        c = len(json.load(open(f"data/logs_{n}.json"))["logs"])
        if c < m["count"]: errs.append(f"logs_{n}: {c} logs < committed {m['count']}")
# built page
h = open("site/index.html").read()
for marker in ('id="hero"', "const D={", "Affected wallets", 'id="qform"', "</footer>"):
    if marker not in h: errs.append(f"site/index.html lacks {marker!r}")
if len(re.findall(r'<button type="button" class="csv" data-csv=', h)) != 8: errs.append("expected 8 CSV buttons")
if re.search(r'<details class="mdet"[^>]*\bopen', h): errs.append("a Methodology <details> is open by default")
if 'id="headline"' not in h: errs.append("headline section missing")
try:
    with open("site/og.png", "rb") as f: hd = f.read(24)
    if struct.unpack(">II", hd[16:24]) != (1200, 630): errs.append("og.png is not 1200x630")
except Exception as e: errs.append(f"og.png unreadable: {e}")
for f in ("favicon.svg", "apple-touch-icon.png"):
    if not os.path.exists("site/" + f): errs.append(f"site/{f} missing")
print(json.dumps({"as_of": b["as_of"], "prev_as_of": prev and prev["blocks"]["as_of"], "redeem_events": t["redeem_events"],
                  "errors": errs, "warnings": warns}, indent=1))
sys.exit(1 if errs else 0)
