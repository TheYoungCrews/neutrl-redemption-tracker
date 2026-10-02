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
    return u.astimezone(ET).strftime("%b %-d, %Y, %-I:%M %p ET") + " (" + u.strftime("%H:%M UTC") + ")"
def et(t, f="%b %-d, %Y, %-I:%M %p ET"):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).astimezone(ET).strftime(f)
b = d["blocks"]
times = {k: fmt_ts(b[k + "_ts"]) for k in ["snapshot", "freeze", "reopen_cooldown", "redeem_open", "as_of", "first_redeem", "last_redeem"]}
times["as_of_short"] = et(b["as_of_ts"])
if d.get("strata") and d["strata"].get("wipe_ts"):
    times["wipe"] = fmt_ts(d["strata"]["wipe_ts"])
try: times["generated"] = fmt_ts(datetime.datetime.fromisoformat(d["generated_at_utc"]).timestamp())
except (KeyError, TypeError, ValueError): pass
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
        if h.get("as_of_block", 1 << 62) < b["as_of"] and cur_ts - h.get("as_of_ts", cur_ts) >= 12 * 3600:
            prev, prev_kind = h, "daily"   # append-only: last match = most recent (>= 12h old, so it is a real "previous day")
prev_out = None
if prev:
    try:
        prev_out = {"as_of_block": prev["as_of_block"], "as_of_ts": prev["as_of_ts"], "as_of_short": et(prev["as_of_ts"], "%b %-d, %-I:%M %p ET"),
                    "kind": prev_kind, "label": "in 24h" if prev_kind == "24h" else "since " + et(prev["as_of_ts"], "%b %-d"),
                    "all_pct_capital": prev["all"]["pct_capital"], "snusd_pct_capital": prev["snusd"]["pct_capital"],
                    "usdc_paid_total": prev["usdc_paid_total"], "usdc_reserve_now": prev["usdc_reserve_now"],
                    "unique_redeemers": prev.get("unique_redeemers"),
                    "snusd_redeemed_wallets": (prev.get("snusd") or {}).get("redeemed_wallets"),
                    "strata": prev.get("strata")}
    except (KeyError, TypeError) as e:
        print("history entry unusable, deltas hidden:", e); prev_out = None

payload = dict(d); payload["times"] = times; payload["prev"] = prev_out
payload["site"] = {"url": SITE_URL, "close_date": REDEMPTION_CLOSE.isoformat()}

# ---------------------------------------------------------------- hero ring (one dot per sNUSD snapshot wallet)
# redeemed = summary_snusd.redeemed_wallets; latest = redeemed sNUSD wallets whose FIRST redemption is within the 24h
# before the as-of block (compute.py: snusd_wallet_first_redeem_ts). No timestamps in the data -> no "latest" dots.
_first = d.get("snusd_wallet_first_redeem_ts") or []
ring = {"wallets": d["summary_snusd"]["wallets"], "redeemed": d["summary_snusd"]["redeemed_wallets"],
        "latest": sum(1 for t in _first if t > cur_ts - 24 * 3600), "latest_since_ts": cur_ts - 24 * 3600}
payload["ring"] = ring
payload.pop("snusd_wallet_first_redeem_ts", None)   # only the derived counts are embedded in the page

# ---------------------------------------------------------------- meta tags
def usdc_short(v):
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.1f}k" if v >= 1e3 else f"${v:.0f}"
SA, SS, T = d["summary_all"], d["summary_snusd"], d["totals"]
meta_desc = (f"{SA['pct_capital']:.1f}% of snapshot NUSD/sNUSD capital redeemed (sNUSD holders {SS['pct_capital']:.1f}%). "
             f"{usdc_short(T['usdc_paid'])} USDC paid out at 0.51 USDC per NUSD, {usdc_short(T['usdc_reserve_now'])} left in the reserve, "
             f"{days_left} days left until the expected Nov 14, 2026 close. Onchain data as of {as_of_date.strftime('%b %-d, %Y')}, updated hourly. Check your wallet.")
og_desc = ("Neutrl froze NUSD and sNUSD on Aug 13, 2026. Redemptions reopened Sep 17 at 0.51 USDC per NUSD and are expected to close Nov 14. "
           "Onchain tracker, updated hourly, of how many freeze-snapshot holders have redeemed.")
tokens = {"__SITE_URL__": SITE_URL, "__META_DESC__": meta_desc, "__OG_DESC__": og_desc,
          "__OG_IMAGE__": f"{SITE_URL}og.png?v=__OG_HASH__",   # filled in after og.png is rendered
          "__OG_ALT__": (f"Neutrl Redemption Tracker share card: dot ring of {SS['redeemed_wallets']:,} of {SS['wallets']:,} sNUSD snapshot wallets redeemed; "
                         f"{SA['pct_capital']:.1f}% of snapshot capital redeemed (all holders), "
                         f"{SS['pct_capital']:.1f}% (sNUSD holders); {usdc_short(T['usdc_paid'])} USDC paid out; {usdc_short(T['usdc_reserve_now'])} USDC left in reserve; "
                         f"{days_left} days left until the expected Nov 14, 2026 close; onchain data as of {as_of_date.strftime('%b %-d, %Y')}, updated hourly")}
tpl = open("template.html").read()
for k, v in tokens.items():
    tpl = tpl.replace(k, html.escape(v, quote=True))
page = tpl.replace("/*__DATA__*/null", json.dumps(payload, separators=(",", ":")))
os.makedirs("site", exist_ok=True)
open("site/index.html", "w").write(page)
print("wrote site/index.html", len(page), "bytes; as of block", b["as_of"], times["as_of"], "| previous run:",
      f"block {prev_out['as_of_block']} ({prev_out['kind']})" if prev_out else "none (deltas hidden)")

# ---------------------------------------------------------------- favicon (static SVG: neutral dot-ring mark, font-independent)
def _mark_svg(size=64, rx=14, bg=True):
    import math
    c, r = size / 2, size * 0.30
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">']
    if bg: out.append(f'<rect width="{size}" height="{size}" rx="{rx}" fill="#131119"/>')
    for k in range(14):
        a = k / 14 * 2 * math.pi
        out.append(f'<circle cx="{c + r * math.sin(a):.2f}" cy="{c - r * math.cos(a):.2f}" r="{size * 0.055:.2f}" fill="{"#5BE49B" if k < 5 else "#3A3546"}"/>')
    out.append(f'<circle cx="{c}" cy="{c}" r="{size * 0.08:.2f}" fill="#F2EEE6"/></svg>')
    return "".join(out)
FAVICON = _mark_svg()
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

# ---------------------------------------------------------------- social card (1200x630), same look as the page hero
def ring_svg(n, lit, latest, size=440, r0=116, r1=212):
    """Dot ring: one dot per sNUSD snapshot wallet; same geometry as ringDots() in template.html."""
    import math
    c = size / 2; s = math.sqrt(math.pi * (r1 * r1 - r0 * r0) / max(n, 1)); K = max(1, int((r1 - r0) // s) + 1)
    rs = [(r0 + r1) / 2] if K == 1 else [r0 + (r1 - r0) * k / (K - 1) for k in range(K)]
    tot = sum(rs); cnt = [int(n * r // tot) for r in rs]; left = n - sum(cnt); k = K - 1
    while left > 0: cnt[k] += 1; left -= 1; k = (k - 1) % K
    dots = []
    for k, (r, m) in enumerate(zip(rs, cnt)):
        for j in range(m):
            a = (j + (0.5 if k % 2 else 0)) / m * 2 * math.pi
            dots.append((a, r, c + r * math.sin(a), c - r * math.cos(a)))
    dots.sort(key=lambda t: (t[0], t[1])); dr = min(3.4, s * 0.26); lit = min(lit, n); latest = min(latest, lit)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">']
    for i, (_, _, x, y) in enumerate(dots):
        col = "#5BE49B" if i < lit - latest else "#B69CFF" if i < lit else "#2B2736"
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{dr if i < lit else dr * .85:.2f}" fill="{col}"/>')
    out.append(f'<line x1="{c}" y1="{c - r0 + 14}" x2="{c}" y2="{c - r1 - 6}" stroke="#F2EEE6" stroke-opacity=".35"/><circle cx="{c}" cy="{c}" r="98" fill="none" stroke="#26222F"/></svg>')
    return "".join(out)
FONTS = ('<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Geist:wght@400;500;600'
         '&family=Geist+Mono:wght@400;500&display=block" rel="stylesheet">')
def og_html():
    pa, ps = SA["pct_capital"], SS["pct_capital"]
    host = SITE_URL.split("://", 1)[1].rstrip("/")
    big = lambda v: re.sub(r"([Mk])$", r"<small>\1</small>", v)
    days_txt = f"{days_left}" if days_left != 1 else "1"
    return f"""<!doctype html><html><head><meta charset="utf-8">{FONTS}<style>
*{{box-sizing:border-box;margin:0}}html,body{{width:1200px;height:630px;overflow:hidden}}
body{{background:radial-gradient(rgba(242,238,230,.06) 1px,transparent 1.2px) 0 0/22px 22px,#0B0A0F;color:#F2EEE6;font-family:Geist,Inter,system-ui,sans-serif;
padding:48px 60px 40px;display:grid;grid-template-columns:1fr 430px;grid-template-rows:auto 1fr auto;column-gap:30px;-webkit-font-smoothing:antialiased}}
.brand{{grid-column:1/-1;display:flex;align-items:center;gap:14px}}.brand svg{{width:40px;height:40px}}.brand b{{font-size:21px;font-weight:500;letter-spacing:-.01em}}
.brand span{{font:400 16px "Geist Mono",monospace;color:#8F899B;margin-left:6px}}
.l{{align-self:center}}.kick{{font:500 16px "Geist Mono",monospace;color:#5BE49B;letter-spacing:.02em}}
h1{{font:400 64px/1 "Instrument Serif",Georgia,serif;letter-spacing:-.02em;margin:16px 0 26px}}h1 em{{color:#C2BCCB}}
.k{{display:flex;align-items:baseline;gap:16px}}.k .v{{font:500 92px/1 "Geist Mono",monospace;letter-spacing:-.06em;color:#5BE49B}}
.k .t{{font-size:21px;color:#C2BCCB;line-height:1.3}}.k .t b{{color:#F2EEE6;font-weight:500}}
.row{{display:flex;gap:34px;margin-top:26px}}.row div{{font:400 15px "Geist Mono",monospace;color:#8F899B}}.row b{{display:block;font:500 30px "Geist Mono",monospace;letter-spacing:-.04em;color:#F2EEE6;margin-bottom:4px}}
.row b small{{font-size:.55em;color:#8F899B;margin-left:2px}}
.r{{position:relative;align-self:center;width:430px;height:430px}}.r svg{{width:430px;height:430px;display:block}}
.c{{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}}
.c .n{{font:500 60px/1 "Geist Mono",monospace;letter-spacing:-.05em}}.c .n span{{color:#8F899B;font-size:.42em}}
.c .s{{font:400 15px/1.4 "Geist Mono",monospace;color:#8F899B;margin-top:8px}}.c .p{{font:500 16px "Geist Mono",monospace;color:#5BE49B;margin-top:10px;padding:6px 12px;border-radius:999px;background:rgba(91,228,155,.1)}}
.f{{grid-column:1/-1;display:flex;justify-content:space-between;font:400 16px "Geist Mono",monospace;color:#8F899B;border-top:1px solid #1C1924;padding-top:16px}}.f b{{color:#F2EEE6;font-weight:400}}
</style></head><body>
<div class="brand">{_mark_svg(40, 10)}<b>Neutrl Redemption Tracker</b><span>independent, onchain</span></div>
<div class="l"><div class="kick">NUSD / sNUSD &middot; redemptions since the freeze</div>
<h1>Neutrl froze on Aug&nbsp;13.<br><em>Who has redeemed since?</em></h1>
<div class="k"><div class="v">{pa:.1f}%</div><div class="t">of snapshot capital redeemed<br>sNUSD holders <b>{ps:.1f}%</b></div></div>
<div class="row"><div><b>{big(usdc_short(T["usdc_paid"]))}</b>USDC paid out</div><div><b>{big(usdc_short(T["usdc_reserve_now"]))}</b>left in reserve</div><div><b>{days_txt}</b>days left (Nov 14)</div></div></div>
<div class="r">{ring_svg(ring["wallets"], ring["redeemed"], ring["latest"])}<div class="c"><div class="n">{ring["redeemed"]:,}<span>/{ring["wallets"]:,}</span></div><div class="s">sNUSD wallets<br>redeemed</div><div class="p">{SS["pct_wallets"]:.1f}%</div></div></div>
<div class="f"><span>Onchain data as of <b>{as_of_date.strftime("%b %-d, %Y")}</b> &middot; updated hourly</span><b>{html.escape(host)}</b></div>
</body></html>"""
if chrome:
    sz = shoot(og_html(), "site/og.png", 1200, 630)
    print("og.png", sz, "OK" if sz == (1200, 630) else "WARNING: unexpected size")
    icon = f'<!doctype html><html><head><style>*{{margin:0}}html,body{{width:180px;height:180px;overflow:hidden;background:#131119}}svg{{display:block;width:180px;height:180px}}</style></head><body>{_mark_svg(180, 0)}</body></html>'
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
        open("site/index.html", "w").write(dom if dom.lstrip()[:9].lower() == "<!doctype" else "<!doctype html>\n" + dom)
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
