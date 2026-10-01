"""Render site/index.html from data/dashboard_data.json (single static page, data embedded).
Also writes site/og.png (1200x630 social card), site/favicon.svg and site/apple-touch-icon.png on every build.
publish_pages.sh copies everything listed in SITE_ASSETS to the GitHub Pages repo."""
import json, datetime, html, os, re, shutil, struct, subprocess, http.server, threading, functools
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- config
SITE_URL = "https://theyoungcrews.github.io/neutrl-redemption-tracker/"   # absolute URL used for og:url / og:image
REDEMPTION_CLOSE = datetime.date(2026, 11, 14)  # Neutrl: "expected to remain open until 14 November 2026"; counts to END of this day (ET)
SITE_ASSETS = ["index.html", "og.png", "favicon.svg", "apple-touch-icon.png"]  # files publish_pages.sh ships

# ---------------------------------------------------------------- ANALYTICS HOOK (Cloudflare Web Analytics enabled)
# Both options are cookieless and need no consent banner. Fill in ONE value and rebuild/publish; nothing else changes.
#   GoatCounter: create a free site at https://www.goatcounter.com/signup -> your code is the subdomain,
#                e.g. "neutrl" for https://neutrl.goatcounter.com
#   Cloudflare Web Analytics: dash.cloudflare.com -> Analytics & Logs -> Web Analytics -> Add a site
#                (hostname theyoungcrews.github.io) -> copy the token from the JS snippet.
# Environment variables of the same name override these (handy for testing).
GOATCOUNTER_CODE = os.environ.get("GOATCOUNTER_CODE", "")    # e.g. "neutrl"
CF_ANALYTICS_TOKEN = os.environ.get("CF_ANALYTICS_TOKEN", "16587e4693794425a3fd008d534bd2eb")  # ENABLED (set up by the user Oct 1, 2026); "" disables

ET = ZoneInfo("America/New_York")
d = json.load(open("data/dashboard_data.json"))
def fmt_ts(t):
    u = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
    return u.astimezone(ET).strftime("%b %-d, %Y %-I:%M %p ET") + " (" + u.strftime("%H:%M UTC") + ")"
def et(t, f="%b %-d, %Y, %-I:%M %p ET"):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).astimezone(ET).strftime(f)
b = d["blocks"]
times = {k: fmt_ts(b[k + "_ts"]) for k in ["snapshot", "freeze", "reopen_cooldown", "redeem_open", "as_of", "first_redeem", "last_redeem"]}
times["as_of_short"] = et(b["as_of_ts"])
as_of_date = datetime.datetime.fromtimestamp(b["as_of_ts"], datetime.timezone.utc).astimezone(ET).date()
days_left = max(0, (REDEMPTION_CLOSE - as_of_date).days + 1)  # includes the as-of day; 1 on Nov 14, 0 after

# ---------------------------------------------------------------- previous snapshot (for hero deltas)
# 1) data/history_hourly.jsonl (appended by the hourly GitHub Actions run): the latest snapshot that is >= ~23.5h
#    older than the current data -> "change in 24h".
# 2) fallback: data/history.jsonl (daily, local morning routine): the most recent entry whose as_of_block is lower
#    than the current one (build.py runs before record_history.py) -> "change since <date>".
# Neither available -> None, and the page hides the deltas.
def read_jsonl(p):
    try: return [json.loads(l) for l in open(p) if l.strip()]
    except FileNotFoundError: return []
prev, prev_kind = None, None
cur_ts = b["as_of_ts"]
hourly = [h for h in read_jsonl("data/history_hourly.jsonl") if h.get("as_of_ts", 1 << 62) <= cur_ts - 23.5 * 3600]
if hourly:
    h = max(hourly, key=lambda h: h["as_of_ts"])
    if cur_ts - h["as_of_ts"] <= 36 * 3600: prev, prev_kind = h, "24h"
if prev is None:
    for h in read_jsonl("data/history.jsonl"):
        if h.get("as_of_block", 1 << 62) < b["as_of"]: prev, prev_kind = h, "daily"   # append-only: last match = most recent
prev_out = None
if prev:
    try:
        prev_out = {"as_of_block": prev["as_of_block"], "as_of_ts": prev["as_of_ts"], "as_of_short": et(prev["as_of_ts"], "%b %-d, %-I:%M %p ET"),
                    "kind": prev_kind, "label": "in 24h" if prev_kind == "24h" else "since " + et(prev["as_of_ts"], "%b %-d"),
                    "all_pct_capital": prev["all"]["pct_capital"], "snusd_pct_capital": prev["snusd"]["pct_capital"],
                    "usdc_paid_total": prev["usdc_paid_total"], "usdc_reserve_now": prev["usdc_reserve_now"],
                    "unique_redeemers": prev.get("unique_redeemers")}
    except (KeyError, TypeError) as e:
        print("history entry unusable, deltas hidden:", e); prev_out = None

payload = dict(d); payload["times"] = times; payload["prev"] = prev_out
payload["site"] = {"url": SITE_URL, "close_date": REDEMPTION_CLOSE.isoformat()}

# ---------------------------------------------------------------- meta tags
def usdc_short(v):
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.1f}k" if v >= 1e3 else f"${v:.0f}"
SA, SS, T = d["summary_all"], d["summary_snusd"], d["totals"]
meta_desc = (f"{SA['pct_capital']:.1f}% of snapshot NUSD/sNUSD capital redeemed (sNUSD holders {SS['pct_capital']:.1f}%). "
             f"{usdc_short(T['usdc_paid'])} USDC paid at 0.51 per NUSD, {usdc_short(T['usdc_reserve_now'])} left in the reserve, "
             f"{days_left} days left until the expected Nov 14, 2026 close. Onchain data as of {as_of_date.strftime('%b %-d, %Y')}. Check your wallet.")
og_desc = ("Neutrl froze NUSD and sNUSD on Aug 13, 2026. Redemptions reopened Sep 17 at 0.51 USDC per NUSD and are expected to close Nov 14. "
           "Onchain tracker, updated hourly, of how many freeze-snapshot holders have redeemed.")
tokens = {"__SITE_URL__": SITE_URL, "__META_DESC__": meta_desc, "__OG_DESC__": og_desc,
          "__OG_IMAGE__": f"{SITE_URL}og.png?v=__OG_HASH__",   # filled in after og.png is rendered
          "__OG_ALT__": f"Neutrl Redemption Tracker: {SA['pct_capital']:.1f}% of snapshot capital redeemed, {usdc_short(T['usdc_paid'])} USDC paid"}
tpl = open("template.html").read()
for k, v in tokens.items():
    tpl = tpl.replace(k, html.escape(v, quote=True))
page = tpl.replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":")))
os.makedirs("site", exist_ok=True)
open("site/index.html", "w").write(page)
print("wrote site/index.html", len(page), "bytes; as of block", b["as_of"], times["as_of"], "| previous run:",
      f"block {prev_out['as_of_block']} ({prev_out['kind']})" if prev_out else "none (deltas hidden)")

# ---------------------------------------------------------------- favicon (static SVG: three bars, font-independent)
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#0f1720"/>'
           '<rect x="12" y="34" width="10" height="18" rx="3" fill="#5aa9ff"/><rect x="27" y="22" width="10" height="30" rx="3" fill="#3fcf6e"/>'
           '<rect x="42" y="12" width="10" height="40" rx="3" fill="#e3b341"/></svg>')
open("site/favicon.svg", "w").write(FAVICON)

chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
def png_size(p):
    with open(p, "rb") as f: h = f.read(24)
    return struct.unpack(">II", h[16:24]) if h[:8] == b"\x89PNG\r\n\x1a\n" else None
def shoot(html_text, out, w, h):
    os.makedirs("build_tmp", exist_ok=True)
    src = os.path.abspath(f"build_tmp/{os.path.basename(out)}.html"); open(src, "w").write(html_text)
    subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                    f"--window-size={w},{h}", "--virtual-time-budget=3000", f"--screenshot={os.path.abspath(out)}", "file://" + src],
                   capture_output=True, text=True, timeout=120)
    return png_size(out) if os.path.exists(out) else None

# ---------------------------------------------------------------- social card (1200x630)
def og_html():
    pa, ps = SA["pct_capital"], SS["pct_capital"]
    host = SITE_URL.split("://", 1)[1].rstrip("/")
    stat = lambda v, k, s="": f'<div class="st"><div class="v">{v}</div><div class="k">{k}</div>{s}</div>'
    bar = lambda p, c: f'<div class="bar"><i style="width:{min(p,100):.1f}%;background:{c}"></i></div>'
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0}}html,body{{width:1200px;height:630px;overflow:hidden}}
body{{background:radial-gradient(900px 420px at 85% -10%,#17406a 0%,transparent 60%),radial-gradient(700px 400px at -10% 110%,#123b2a 0%,transparent 60%),#0b0f14;
color:#e8eef5;font-family:Inter,"IBM Plex Sans",Roboto,sans-serif;padding:56px 64px;display:flex;flex-direction:column}}
.t{{font-size:50px;font-weight:800;letter-spacing:-.02em}}.s{{font-size:23px;color:#8a97a6;margin-top:6px}}
.g1{{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:38px}}.g2{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:22px;margin-top:22px}}
.st{{background:#131a22cc;border:1px solid #243040;border-radius:18px;padding:20px 24px}}
.v{{font-size:54px;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.05}}.g2 .v{{font-size:40px}}
.k{{font-size:20px;color:#9fb0c2;margin-top:6px}}.bar{{height:10px;background:#243040;border-radius:6px;overflow:hidden;margin-top:12px}}.bar i{{display:block;height:100%}}
.f{{margin-top:auto;display:flex;justify-content:space-between;font-size:20px;color:#8a97a6}}.f b{{color:#5aa9ff;font-weight:600}}
</style></head><body>
<div class="t">Neutrl Redemption Tracker</div>
<div class="s">NUSD / sNUSD redemptions at 0.51 USDC &middot; Ethereum mainnet, onchain data</div>
<div class="g1">{stat(f'<span style="color:#3fcf6e">{pa:.1f}%</span>', "of snapshot capital redeemed &middot; all holders", bar(pa, "#3fcf6e"))}
{stat(f'<span style="color:#5aa9ff">{ps:.1f}%</span>', "of snapshot capital redeemed &middot; sNUSD holders", bar(ps, "#5aa9ff"))}</div>
<div class="g2">{stat(usdc_short(T["usdc_paid"]), "USDC paid out")}{stat(usdc_short(T["usdc_reserve_now"]), "USDC left in reserve")}
{stat(f'<span style="color:#e3b341">{days_left}</span>', "days left until expected close (Nov 14, 2026)")}</div>
<div class="f"><span>Onchain data as of {as_of_date.strftime("%b %-d, %Y")} &middot; updated hourly</span><b>{html.escape(host)}</b></div>
</body></html>"""
if chrome:
    sz = shoot(og_html(), "site/og.png", 1200, 630)
    print("og.png", sz, "OK" if sz == (1200, 630) else "WARNING: unexpected size")
    icon = f'<!doctype html><html><head><style>*{{margin:0}}html,body{{width:180px;height:180px;overflow:hidden;background:#0f1720}}svg{{display:block;width:180px;height:180px}}</style></head><body>{FAVICON.replace(" rx=\"14\"", "")}</body></html>'
    print("apple-touch-icon.png", shoot(icon, "site/apple-touch-icon.png", 180, 180))
else:
    print("WARNING: no Chrome found; og.png / apple-touch-icon.png not regenerated")

# og:image cache-buster = hash of the PNG (changes only when the card changes, so X/Discord re-fetch only then)
import hashlib
og_hash = hashlib.sha256(open("site/og.png", "rb").read()).hexdigest()[:10] if os.path.exists("site/og.png") else str(b["as_of"])
s = open("site/index.html").read().replace("__OG_HASH__", og_hash); open("site/index.html", "w").write(s)

# ---------------------------------------------------------------- pre-render
# Run the page's JS once in headless Chrome and save the resulting DOM, so the numbers are
# visible even where JavaScript is blocked (scripts are kept, so charts/toggles/lookup still work).
if chrome:
    os.makedirs("build_tmp", exist_ok=True)
    for f in ("index.html", "favicon.svg"): shutil.copy("site/" + f, "build_tmp/" + f)
    H = functools.partial(http.server.SimpleHTTPRequestHandler, directory="build_tmp")
    class Q(H.func):
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory="build_tmp")); port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    dom = subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--virtual-time-budget=10000", "--dump-dom", f"http://127.0.0.1:{port}/"],
                         capture_output=True, text=True, timeout=120).stdout
    srv.shutdown()
    if 'id="headline"' in dom and "Affected wallets" in dom and "const D=" in dom and 'id="hero"' in dom:
        # Chart.js-modified canvases are reset so charts re-initialise cleanly
        dom = re.sub(r'<canvas id="(\w+)"[^>]*>', r'<canvas id="\1">', dom)
        open("site/index.html", "w").write("<!doctype html>\n" + dom)
        print("pre-rendered with", chrome, len(dom), "bytes")
    else:
        print("pre-render failed; keeping JS-only page")

# ---------------------------------------------------------------- analytics injection (after pre-render, so the
# headless pre-render never registers a page view)
if GOATCOUNTER_CODE:
    snip = ('<script>window.goatcounter={path:function(){return location.pathname}}</script>'  # never log ?address= values
            f'<script data-goatcounter="https://{html.escape(GOATCOUNTER_CODE)}.goatcounter.com/count" async src="https://gc.zgo.at/count.js"></script>')
elif CF_ANALYTICS_TOKEN:
    snip = ('<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
            f"data-cf-beacon='{{\"token\": \"{html.escape(CF_ANALYTICS_TOKEN)}\"}}'></script>")
else:
    snip = "<!-- analytics: disabled (set GOATCOUNTER_CODE or CF_ANALYTICS_TOKEN in build.py) -->"
s = open("site/index.html").read()
if "<!--__ANALYTICS__-->" in s:
    s = s.replace("<!--__ANALYTICS__-->", snip, 1); open("site/index.html", "w").write(s)
print("analytics:", "goatcounter" if GOATCOUNTER_CODE else "cloudflare" if CF_ANALYTICS_TOKEN else "disabled")
