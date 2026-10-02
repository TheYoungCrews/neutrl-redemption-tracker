"""Compute Neutrl NUSD/sNUSD redemption stats from raw logs in data/. Writes data/dashboard_data.json."""
import json, collections, datetime, os
from zoneinfo import ZoneInfo
from rpc import call, eth_call, block

ET = ZoneInfo("America/New_York")
ZERO = "0x" + "0"*40
NUSD = "0xe556aba6fe6036275ec1f87eda296be72c811bce"
SNUSD = "0x08efcc2f3e61185d0ea7f8830b3fec9bfa2ee313"
SILO = "0x6cdfc009ab1c5f8114a8aa0117a7e6fcbb35bb9b"
ASSETLOCK = "0x99161ba892ecae335616624c84faa418f64ff9a6"
REDEMPTION = "0xb3f07d3392102fc23264a78e2a1a8b6421123828"
RESERVE = "0xfee69fa9c94b9d967390a4f4144e2746603ec1ed"
STRATA = "0x3cef2c09c4fad37e9bdd86cd9810c3042fb5de88"
SRNUSD = "0x65a44528e8868166401ea08b549e19552af589db"   # Strata senior tranche (docs.strata.markets)
JRNUSD = "0xfc807058a352b61aeef6a38e2d0fc3990225e772"   # Strata junior tranche (first-loss)
STRATA_CDO = "0x7b6c960cf185fb27ecb91c174fae065978bedd10"
STRATA_WIPE_BLOCK = 26013480  # Accounting update: jrNUSD totalAssets -> 1; srNUSD/NUSD rate -> 1.239711522681
INSTANT_UNSTAKING = "0x4bb8f67d5643e6289c55371dbfd021ddfdaea0f6"  # its Silo cooldown entry is settled by burning NUSD from the Silo, so it is not a real claim

# Key blocks (all verified from onchain events; see README)
FREEZE_BLOCK = 25745732        # sNUSD Paused() tx 0xc25678129b..., 2026-08-13 11:14:59 UTC
SNAP = FREEZE_BLOCK - 1        # snapshot = state at end of the block before the sNUSD pause
REOPEN_COOLDOWN_BLOCK = 25997006  # sNUSD cooldownDuration set to 1s, 2026-09-17 11:47:11 UTC
REDEEM_OPEN_BLOCK = 25997438   # NusdRedemption Unpaused(), 2026-09-17 13:14:23 UTC
RATE = 0.51

TOP = json.load(open("data/topics.json"))
T = {v: k for k, v in TOP.items()}
L = lambda n: json.load(open(f"data/logs_{n}.json"))
meta = json.load(open("data/address_meta.json"))
a40 = lambda t: "0x" + t[-40:]
bn = lambda l: int(l["blockNumber"], 16)
W = lambda h: int(h, 16)

# ---------- block timestamps (cached) ----------
tsf = "data/block_ts.json"
TS = {int(k): v for k, v in (json.load(open(tsf)).items() if os.path.exists(tsf) else [])}
def ts(b):
    if b not in TS: TS[b] = int(block(b)["timestamp"], 16)
    return TS[b]

# ---------- ERC20 balances ----------
def balances(name, upto):
    b = collections.defaultdict(int)
    for l in L(name)["logs"]:
        if l["topics"][0] != T["Transfer(address,address,uint256)"] or bn(l) > upto: continue
        v = W(l["data"]); b[a40(l["topics"][1])] -= v; b[a40(l["topics"][2])] += v
    return b

def cooldowns(upto):
    """pending NUSD in Silo per staker (UnstakeRequested adds, Unstaked clears all)."""
    c = collections.defaultdict(int)
    for l in L("snusd")["logs"]:
        if bn(l) > upto: continue
        s = TOP.get(l["topics"][0])
        if s == "UnstakeRequested(address,uint256,uint256,uint104)":
            c[a40(l["topics"][1])] += W(l["data"][66:130])
        elif s == "Unstaked(address,address,uint256)":
            c[a40(l["topics"][1])] = 0
    return c

def locks(upto):
    """AssetLock per (user, asset) locked amount."""
    k = collections.defaultdict(int)
    for l in L("assetlock")["logs"]:
        if bn(l) > upto: continue
        s = TOP.get(l["topics"][0])
        if s == "AssetLocked(address,address,uint256,uint128)":
            k[(a40(l["topics"][1]), a40(l["topics"][2]))] += W(l["data"][2:66])
        elif s == "AssetUnlocked(address,address,uint256,uint256)":
            k[(a40(l["topics"][1]), a40(l["topics"][2]))] -= W(l["data"][2:66])
    return k

def share_rate(blk):
    return W(eth_call(SNUSD, "0x07a2d13a" + hex(10**18)[2:].rjust(64, "0"), blk)) / 1e18

head = L("redemption")["to_block"]
for n in ["nusd", "snusd", "assetlock", "assetreserve"]:
    head = min(head, L(n)["to_block"])

def classify(a):
    m = meta.get(a)
    if m is None:
        code = call("eth_getCode", [a, "latest"])
        m = {"kind": "eoa" if code == "0x" else ("eip7702" if code.startswith("0xef0100") else "contract"), "code_len": (len(code)-2)//2}
        if m["kind"] == "contract":
            try:
                import requests
                j = requests.get(f"https://eth.blockscout.com/api/v2/addresses/{a}", timeout=30).json()
                m["name"] = j.get("name"); m["proxy"] = j.get("proxy_type")
                m["impl"] = [(i.get("name"), i.get("address_hash") or i.get("address")) for i in (j.get("implementations") or [])]
            except Exception as e: m["err"] = str(e)
        meta[a] = m
    if m["kind"] in ("eoa", "eip7702"): return "wallet", ("EOA" if m["kind"] == "eoa" else "EOA (EIP-7702)")
    impl = [i[0] for i in (m.get("impl") or []) if i[0]]
    if any(x in ("GnosisSafe", "Safe", "GnosisSafeL2") for x in impl): return "wallet", "Safe multisig"
    if m.get("code_len") == 45 and "clone_of" not in m:   # EIP-1167 minimal proxy: read the target from bytecode
        code = call("eth_getCode", [a, "latest"])
        m["clone_of"] = "0x" + code[22:62] if code.startswith("0x363d3d373d3d3d363d73") else None
    if (m.get("clone_of") or "").lower() == "0xca864927dc63651ef7b9198d3bf717c209fe22f7" or \
       any((i[1] or "").lower() == "0xca864927dc63651ef7b9198d3bf717c209fe22f7" for i in (m.get("impl") or [])):
        return "contract", "Strata sNUSD cooldown request (per-user proxy)"
    name = (impl[0] if impl and impl[0] not in ("BeaconProxy",) else None) or m.get("name")
    return "contract", LABELS.get(a, name or "Unverified contract")

LABELS = {
    SNUSD: "sNUSD vault (Neutrl)", SILO: "sNUSD cooldown Silo (Neutrl)", ASSETLOCK: "Neutrl AssetLock (looked-through)",
    STRATA: "Strata sNUSDStrategy (srNUSD/jrNUSD tranches)",
    SRNUSD: "Strata srNUSD (senior tranche)", JRNUSD: "Strata jrNUSD (junior tranche)",
    STRATA_CDO: "Strata NeutrlCDO",
    "0x10c5e7711eaddc1b6b64e40ef1976fc462666409": "Pendle SY-sNUSD", "0x212bfcb33fcdaa603cff2abef25444ae5519dee8": "Pendle SY-sNUSD (2)",
    "0x29ac34026c369d21fe3b2c7735ec986e2880b347": "Pendle SY-NUSD", "0x33305665f69b4642d1275f4ce81c23651674d21c": "Pendle Merkle distributor",
    "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb": "Morpho Blue", "0x4ea52e06f21a1a5d60da88a813c6d0e597d8e7c6": "Euler EVault (esNUSD-3)",
    "0x7e19f0253a564e026c63eeaa9338d6dbddef3b09": "Curve NUSD/USDC pool", "0x000000000004444c5dc75cb358380d2e3de08a90": "Uniswap v4 PoolManager",
    "0xba12222222228d8ba445958a75a0704d566bf2c8": "Balancer V2 Vault",
    "0x350f09f8dc8d8ebb6d604acbe24e6b09524c0054": "LayerZero OFT adapter (Neutrl bridge)", "0x8e14d37b56b3d17e0f3abc3b36aa304868f3476b": "LayerZero OFT adapter v2 (Neutrl bridge)",
    "0x2a3ac59341131f2a0d03af3a867148b2b9b0dcb8": "LayerZero OFT adapter v2 (Neutrl bridge)", "0xe27ead742ea45b7063b69a875c6f99181c323fb6": "LayerZero OFT adapter (Neutrl bridge)",
    "0x0ae0978b868804929fd4c06b3b22d9197b8cd3c6": "Royco tranche kernel", "0xbdf2d357464727ee136a5f81479554f86759993a": "Royco tranche kernel",
    "0xff9da59af6a06f228f38c6bbebcd7e70070d4e2b": "Royco tranche kernel", "0x42bb7215389c6ad1c9d6b38bdbbbcb8c14bae530": "Royco Day kernel",
    "0x63da1229be88fb4d20210147954a1a3e05f2581b": "Royco EntryPoint", "0x77d135a27396ba1647cd8c3af3b2ccf9305a9e8c": "Neutrl YieldDistributor",
    "0x28c7f1d820b7f15eee487064cea46c3317dbcf8a": "Neutrl OVault composer", "0x4bb8f67d5643e6289c55371dbfd021ddfdaea0f6": "InstantUnstaking",
    "0x9008d19f58aabd9ed0d60971565aa8510560ab41": "CoW Protocol settlement",
}

# ---------- snapshot positions (look-through AssetLock + Silo cooldowns) ----------
R_SNAP = share_rate(SNAP); R_NOW = share_rate(head)
def positions(blk, rate):
    nb, sb, cd, lk = balances("nusd", blk), balances("snusd", blk), cooldowns(blk), locks(blk)
    pos = collections.defaultdict(lambda: {"nusd": 0, "snusd": 0, "lock_nusd": 0, "lock_snusd": 0, "cooldown": 0})
    for a, v in nb.items():
        if a in (ZERO, SNUSD, SILO, ASSETLOCK) or v <= 0: continue
        pos[a]["nusd"] = v
    for a, v in sb.items():
        if a in (ZERO, ASSETLOCK) or v <= 0: continue
        pos[a]["snusd"] = v
    for a, v in cd.items():
        if v > 0 and a != INSTANT_UNSTAKING: pos[a]["cooldown"] = v
    for (u, asset), v in lk.items():
        if v <= 0: continue
        pos[u]["lock_nusd" if asset == NUSD else "lock_snusd"] += v
    out = {}
    for a, p in pos.items():
        val = (p["nusd"] + p["lock_nusd"] + p["cooldown"]) / 1e18 + (p["snusd"] + p["lock_snusd"]) / 1e18 * rate
        if val <= 0: continue
        out[a] = dict(p, value=val, snusd_value=(p["snusd"] + p["lock_snusd"]) / 1e18 * rate)
    checks = {
        "silo_nusd_balance": nb[SILO] / 1e18, "silo_cooldowns_sum": sum(v for a, v in cd.items() if v > 0 and a != INSTANT_UNSTAKING) / 1e18,
        "assetlock_snusd_balance": sb[ASSETLOCK] / 1e18, "assetlock_snusd_locks_sum": sum(v for (u, a), v in lk.items() if a == SNUSD) / 1e18,
        "assetlock_nusd_balance": nb[ASSETLOCK] / 1e18, "assetlock_nusd_locks_sum": sum(v for (u, a), v in lk.items() if a == NUSD) / 1e18,
        "nusd_supply": -nb[ZERO] / 1e18, "snusd_supply": -sb[ZERO] / 1e18, "snusd_vault_nusd": nb[SNUSD] / 1e18,
        "snusd_raw_holders": sum(1 for a, v in sb.items() if a != ZERO and v > 0),
        "nusd_raw_holders": sum(1 for a, v in nb.items() if a != ZERO and v > 0),
    }
    return out, checks

snap, snap_checks = positions(SNAP, R_SNAP)
now, now_checks = positions(head, R_NOW)
onchain_supply = W(eth_call(NUSD, "0x18160ddd", SNAP)) / 1e18
assert abs(onchain_supply - snap_checks["nusd_supply"]) < 1e-6, "NUSD supply reconstruction mismatch"

# ---------- redemptions ----------
reds = []
for l in L("redemption")["logs"]:
    if TOP.get(l["topics"][0]) != "Redeemed(address,uint256,uint256)": continue
    b = bn(l)
    reds.append({"user": a40(l["topics"][1]), "nusd": W(l["data"][2:66]) / 1e18, "usdc": W(l["data"][66:130]) / 1e6, "block": b, "ts": ts(b), "tx": l["transactionHash"]})
signed = {a40(l["topics"][1]) for l in L("redemption")["logs"] if TOP.get(l["topics"][0]) == "MessageSigned(address,bytes32,uint64)"}
by_user = collections.defaultdict(lambda: {"nusd": 0.0, "usdc": 0.0, "n": 0, "first_ts": None})
for r in reds:
    u = by_user[r["user"]]; u["nusd"] += r["nusd"]; u["usdc"] += r["usdc"]; u["n"] += 1
    u["first_ts"] = r["ts"] if u["first_ts"] is None else min(u["first_ts"], r["ts"])

# sNUSD unstaked since reopen, by staker
unstk = collections.defaultdict(lambda: [0, 0])
for l in L("snusd")["logs"]:
    if bn(l) < REOPEN_COOLDOWN_BLOCK or TOP.get(l["topics"][0]) != "UnstakeRequested(address,uint256,uint256,uint104)": continue
    s = a40(l["topics"][1]); unstk[s][0] += W(l["data"][2:66]) / 1e18; unstk[s][1] += W(l["data"][66:130]) / 1e18

# ---------- restrictions (sNUSD FULL_RESTRICTED_STAKER_ROLE, NUSD denylist) as of head ----------
from Crypto.Hash import keccak
FULL_RESTRICTED = "0x" + keccak.new(digest_bits=256, data=b"FULL_RESTRICTED_STAKER_ROLE").hexdigest()
SOFT_RESTRICTED = "0x" + keccak.new(digest_bits=256, data=b"SOFT_RESTRICTED_STAKER_ROLE").hexdigest()
# Neutral labels for restricted addresses (owner-approved, from /workspace/inv/findings_restricted_addresses.md; identities are
# verified contract names / Neutrl's own adapters / the public Mar 19, 2026 front-end hijack). Unknown new entries stay unlabeled.
RESTRICTED_LABELS = {
    "0xe27ead742ea45b7063b69a875c6f99181c323fb6": "Neutrl bridge adapter v1 (sNUSD)",
    "0x350f09f8dc8d8ebb6d604acbe24e6b09524c0054": "Neutrl bridge adapter v1 (NUSD)",
    "0x8e14d37b56b3d17e0f3abc3b36aa304868f3476b": "Neutrl bridge adapter v2 (NUSD)",
    "0x7e19f0253a564e026c63eeaa9338d6dbddef3b09": "Curve NUSD/USDC pool",
    "0x000000000004444c5dc75cb358380d2e3de08a90": "Uniswap v4 PoolManager",
    "0xba12222222228d8ba445958a75a0704d566bf2c8": "Balancer V2 Vault",
    "0xafb2423f447d3e16931164c9970b9741aab1723e": "Mar 19 website-hijack drainer",
    "0xf1a50bbeba19a85db20432c6c201aa89604dfd2b": "Mar 19 website-hijack drainer",
    "0xfd582d41fcc008ff5cbd2996043de3ce25e7543e": "Mar 19 website-hijack drainer",
    "0xbe8dac0a6446a2e7d31f96d0218dcddfbce65bf4": "Holder wallet",
    "0x6924189eb810b798d3a7c49f2f27b2bdeffa0ca8": "Holder wallet",
    "0xb0bfdc336537730ad2c44083c0f7c545aac79a9a": "Holder wallet",
}
# address -> {"snusd": entry, "nusd": entry}: an address can carry BOTH restrictions (each kept with its own date/tx)
restricted = collections.defaultdict(dict); restrictions_lifted = 0
for l in L("snusd")["logs"]:
    sg = TOP.get(l["topics"][0])
    if sg in ("RoleGranted(bytes32,address,address)", "RoleRevoked(bytes32,address,address)") and l["topics"][1] == FULL_RESTRICTED:
        a = a40(l["topics"][2])
        if sg.startswith("RoleGranted"):
            restricted[a]["snusd"] = {"type": "sNUSD full-restricted (blacklisted)", "block": bn(l), "ts": ts(bn(l)), "tx": l["transactionHash"],
                                      "set_by": a40(l["topics"][3]) if len(l["topics"]) > 3 else None}
        else: restricted[a].pop("snusd", None); restrictions_lifted += 1
for l in L("nusd")["logs"]:
    sg = TOP.get(l["topics"][0]); a = a40(l["topics"][1]) if len(l["topics"]) > 1 else None
    if sg == "AddedToDenylist(address)": restricted[a]["nusd"] = {"type": "NUSD denylisted", "block": bn(l), "ts": ts(bn(l)), "tx": l["transactionHash"], "set_by": None}
    elif sg == "RemovedFromDenylist(address)": restricted[a].pop("nusd", None); restrictions_lifted += 1
restricted = {a: k for a, k in restricted.items() if k}
for k in restricted.values():          # AddedToDenylist has no sender field: the setter is the tx sender
    for e in k.values():
        if e["set_by"] is None:
            try: e["set_by"] = call("eth_getTransactionByHash", [e["tx"]])["from"].lower()
            except Exception as ex: print("set_by lookup failed", e["tx"], ex)
restricted_type = lambda a: "; ".join(e["type"] for e in sorted(restricted.get(a, {}).values(), key=lambda e: e["block"])) or None

# ---------- wallet universe ----------
def cls(a): return classify(a)
rows = []
for a, p in snap.items():
    kind, label = cls(a)
    r = by_user.get(a)
    cur = now.get(a, {}).get("value", 0.0)
    red = r["nusd"] if r else 0.0
    rows.append({"address": a, "kind": kind, "label": label, "value": p["value"], "snusd_value": p["snusd_value"],
                 "nusd": p["nusd"] / 1e18, "snusd": p["snusd"] / 1e18, "lock_nusd": p["lock_nusd"] / 1e18, "lock_snusd": p["lock_snusd"] / 1e18,
                 "cooldown": p["cooldown"] / 1e18, "redeemed_nusd": red, "usdc": r["usdc"] if r else 0.0,
                 "redeemed": bool(r), "remaining_value": cur, "signed": a in signed,
                 "restricted": restricted_type(a)})

def status(r):
    if r["redeemed"]:
        return "Redeemed" if r["remaining_value"] < 0.01 * r["value"] + 1 else "Redeemed (still holds some)"
    if r["remaining_value"] < 0.05 * r["value"]: return "Not redeemed (tokens moved out)"
    return "Not redeemed"
for r in rows: r["status"] = status(r)

wallets = [r for r in rows if r["kind"] == "wallet"]
contracts = [r for r in rows if r["kind"] == "contract"]
snusd_wallets = [r for r in wallets if r["snusd_value"] > 0]

def summarize(rs):
    aff = sum(r["value"] for r in rs)
    red_rs = [r for r in rs if r["redeemed"]]
    red_cap = sum(min(r["redeemed_nusd"], r["value"]) for r in red_rs)
    red_raw = sum(r["redeemed_nusd"] for r in red_rs)
    return {"wallets": len(rs), "redeemed_wallets": len(red_rs), "pct_wallets": (len(red_rs) / len(rs) * 100) if rs else None,
            "affected_usd": aff, "redeemed_usd_capped": red_cap, "redeemed_usd_raw": red_raw, "pct_capital": (red_cap / aff * 100) if aff else None,
            "usdc_paid": sum(r["usdc"] for r in red_rs)}

S_all = summarize(wallets); S_snusd = summarize(snusd_wallets)
S_all_unrestricted = summarize([r for r in wallets if not r["restricted"]])
S_snusd_unrestricted = summarize([r for r in snusd_wallets if not r["restricted"]])
restricted_rows = [r for r in rows if r["restricted"]]
DUST = 1.0  # USD
S_all_nodust = summarize([r for r in wallets if r["value"] >= DUST]); S_snusd_nodust = summarize([r for r in snusd_wallets if r["value"] >= DUST])

# Buckets by pre-incident USD value
BK = [("< $1k", 0, 1e3), ("$1k–$10k", 1e3, 1e4), ("$10k–$100k", 1e4, 1e5), ("$100k–$1M", 1e5, 1e6), ("> $1M", 1e6, 1e18)]
def buckets(rs):
    out = []
    for name, lo, hi in BK:
        b = [r for r in rs if lo <= r["value"] < hi]
        s = summarize(b); s["bucket"] = name; out.append(s)
    return out

# Redeemers not in the snapshot wallet set
snap_set = set(snap)
outsiders = {u: v for u, v in by_user.items() if u not in snap_set}
contract_redeemers = {u: v for u, v in by_user.items() if u in snap_set and cls(u)[0] == "contract"}

# Where did outsiders get their tokens after the snapshot? (inbound NUSD/sNUSD transfers after SNAP, by sender label)
src = collections.defaultdict(float)
osset = set(outsiders)
for name, mult in (("nusd", 1.0), ("snusd", R_NOW)):
    for l in L(name)["logs"]:
        if l["topics"][0] != T["Transfer(address,address,uint256)"] or bn(l) <= SNAP: continue
        to = a40(l["topics"][2]); fr = a40(l["topics"][1])
        if to not in osset or fr == ZERO: continue
        if name == "nusd" and fr in (SILO, SNUSD, ASSETLOCK): continue   # unstake/unlock paths, counted at origin
        k, lab = cls(fr)
        src[lab if k == "contract" else "Other wallets (transfers/OTC)"] += W(l["data"]) / 1e18 * mult
outsider_sources = sorted(([k, v] for k, v in src.items()), key=lambda x: -x[1])

# ---------- daily series (ET dates) ----------
first_day = datetime.datetime.fromtimestamp(ts(REDEEM_OPEN_BLOCK), ET).date()
last_day = datetime.datetime.fromtimestamp(ts(head), ET).date()
seen = set(); seen_snap = set(); cum_n = cum_u = 0.0; cum_snap_cap = 0.0
daily = []; byday = collections.defaultdict(list)
for r in sorted(reds, key=lambda r: (r["block"])):
    byday[datetime.datetime.fromtimestamp(r["ts"], ET).date()].append(r)
snapval = {r["address"]: r["value"] for r in wallets}
credited = collections.defaultdict(float)
d = first_day
while d <= last_day:
    day = byday.get(d, [])
    new_w = 0
    for r in day:
        if r["user"] not in seen: seen.add(r["user"]); new_w += 1
        cum_n += r["nusd"]; cum_u += r["usdc"]
        if r["user"] in snapval:
            seen_snap.add(r["user"])
            add = min(r["nusd"], snapval[r["user"]] - credited[r["user"]]); add = max(add, 0)
            credited[r["user"]] += add; cum_snap_cap += add
    daily.append({"date": d.isoformat(), "redemptions": len(day), "new_wallets": new_w, "nusd": sum(r["nusd"] for r in day), "usdc": sum(r["usdc"] for r in day),
                  "cum_wallets": len(seen), "cum_snapshot_wallets": len(seen_snap), "cum_nusd": cum_n, "cum_usdc": cum_u, "cum_snapshot_capital": cum_snap_cap})
    d += datetime.timedelta(days=1)

# ---------- USDC reserve ----------
ur = json.load(open("data/logs_usdc_reserve.json"))
usdc_funded = sum(W(l["data"]) for l in ur["in"]) / 1e6
usdc_out = sum(W(l["data"]) for l in ur["out"]) / 1e6
usdc_reserve_now = W(eth_call("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "0x70a08231" + "0"*24 + RESERVE[2:], head)) / 1e6

tot_n = sum(r["nusd"] for r in reds); tot_u = sum(r["usdc"] for r in reds)
contract_tbl = sorted(contracts, key=lambda r: -r["value"])
proto_value = sum(r["value"] for r in contracts)
strata_unstaked = unstk.get(STRATA, [0, 0])

# ---------- Strata Neutrl market (srNUSD / jrNUSD) ----------
# Contracts: docs.strata.markets/technical-documentation/contracts-details (verified proxies on Etherscan).
# Metrics from ERC-4626 totalSupply / totalAssets / convertToAssets, strategy sNUSD balanceOf, and
# Withdraw / Deposit / DepositsStateChanged / WithdrawalsStateChanged events. Strata exits settle in
# sNUSD/NUSD (not Neutrl portal USDC); USDC Transfer involving strategy/CDO/tranches since reopen = 0.
def _sel(sig):
    return "0x" + keccak.new(digest_bits=256, data=sig.encode()).hexdigest()[:8]
_ONE = hex(10**18)[2:].rjust(64, "0")
def _tranche_at(addr, blk):
    return {
        "supply": W(eth_call(addr, "0x18160ddd", blk)) / 1e18,
        "assets_nusd": W(eth_call(addr, _sel("totalAssets()"), blk)) / 1e18,
        "exchange_rate_nusd": W(eth_call(addr, _sel("convertToAssets(uint256)") + _ONE, blk)) / 1e18,
    }
def _snusd_bal(holder, blk):
    return W(eth_call(SNUSD, "0x70a08231" + "0"*24 + holder[2:], blk)) / 1e18
def _wd_stats(name, from_blk, to_blk):
    path = f"data/logs_{name}.json"
    if not os.path.exists(path):
        return {"events": 0, "assets_nusd": 0.0, "shares": 0.0, "unique_owners": 0}
    assets = shares = n = 0; owners = set()
    for l in json.load(open(path))["logs"]:
        if TOP.get(l["topics"][0]) != "Withdraw(address,address,address,uint256,uint256)": continue
        b = bn(l)
        if b < from_blk or b > to_blk: continue
        data = l["data"][2:]
        assets += W("0x" + data[0:64]); shares += W("0x" + data[64:128]); n += 1
        if len(l["topics"]) > 3: owners.add(a40(l["topics"][3]))
    return {"events": n, "assets_nusd": assets / 1e18, "shares": shares / 1e18, "unique_owners": len(owners)}
def _cdo_flags(upto):
    path = "data/logs_strata_cdo.json"
    dep = {SRNUSD: None, JRNUSD: None}; wdr = {SRNUSD: None, JRNUSD: None}
    if not os.path.exists(path):
        return {"deposits_enabled": dep, "withdrawals_enabled": wdr}
    for l in json.load(open(path))["logs"]:
        if bn(l) > upto: continue
        sg = TOP.get(l["topics"][0]); tranche = a40(l["topics"][1]) if len(l["topics"]) > 1 else None
        if tranche not in (SRNUSD, JRNUSD): continue
        enabled = bool(W(l["data"]))
        if sg == "DepositsStateChanged(address,bool)": dep[tranche] = enabled
        elif sg == "WithdrawalsStateChanged(address,bool)": wdr[tranche] = enabled
    return {"deposits_enabled": dep, "withdrawals_enabled": wdr}
def _coverage(strat_nusd, sr_assets):
    return (strat_nusd / sr_assets) if sr_assets and sr_assets > 0 else None

sr_snap = _tranche_at(SRNUSD, SNAP); jr_snap = _tranche_at(JRNUSD, SNAP)
sr_now = _tranche_at(SRNUSD, head); jr_now = _tranche_at(JRNUSD, head)
sr_wipe = _tranche_at(SRNUSD, STRATA_WIPE_BLOCK); jr_wipe = _tranche_at(JRNUSD, STRATA_WIPE_BLOCK)
snusd_strat_snap = _snusd_bal(STRATA, SNAP)
snusd_strat_now = _snusd_bal(STRATA, head)
snusd_strat_wipe = _snusd_bal(STRATA, STRATA_WIPE_BLOCK)
strat_nusd_snap = snusd_strat_snap * R_SNAP
strat_nusd_now = snusd_strat_now * R_NOW
strat_nusd_wipe = snusd_strat_wipe * share_rate(STRATA_WIPE_BLOCK)
flags_now = _cdo_flags(head)
sr_wd_post = _wd_stats("srnusd", STRATA_WIPE_BLOCK, head)
jr_wd_post = _wd_stats("jrnusd", STRATA_WIPE_BLOCK, head)
jr_wiped = jr_now["exchange_rate_nusd"] < 1e-4 or jr_now["assets_nusd"] <= 1.0
strata = {
    "contracts": {
        "srNUSD": SRNUSD, "jrNUSD": JRNUSD, "strategy": STRATA, "cdo": STRATA_CDO,
        "docs": "https://docs.strata.markets/technical-documentation/contracts-details",
    },
    "wipe_block": STRATA_WIPE_BLOCK, "wipe_ts": ts(STRATA_WIPE_BLOCK),
    "settlement_asset": "sNUSD/NUSD",
    "usdc_paid_via_strata": 0.0,
    "deposits_enabled": {"srNUSD": flags_now["deposits_enabled"].get(SRNUSD), "jrNUSD": flags_now["deposits_enabled"].get(JRNUSD)},
    "withdrawals_enabled": {"srNUSD": flags_now["withdrawals_enabled"].get(SRNUSD), "jrNUSD": flags_now["withdrawals_enabled"].get(JRNUSD)},
    "senior": {
        "symbol": "srNUSD", "address": SRNUSD,
        "snapshot": sr_snap, "at_wipe": sr_wipe, "now": sr_now,
        "withdrawals_since_wipe": sr_wd_post,
        "supply_change_since_wipe": sr_now["supply"] - sr_wipe["supply"],
    },
    "junior": {
        "symbol": "jrNUSD", "address": JRNUSD, "first_loss": True, "wiped": jr_wiped,
        "snapshot": jr_snap, "at_wipe": jr_wipe, "now": jr_now,
        "withdrawals_since_wipe": jr_wd_post,
        "supply_change_since_wipe": jr_now["supply"] - jr_wipe["supply"],
        "note": ("Junior is first-loss. After the wipe accounting update, onchain jrNUSD/NUSD is ~0; "
                 "outstanding shares remain but claim essentially nothing. Not a Neutrl-portal redemption %.") if jr_wiped else None,
    },
    "strategy": {
        "address": STRATA,
        "snusd_balance_snapshot": snusd_strat_snap,
        "snusd_balance_at_wipe": snusd_strat_wipe,
        "snusd_balance_now": snusd_strat_now,
        "nusd_value_snapshot": strat_nusd_snap,
        "nusd_value_at_wipe": strat_nusd_wipe,
        "nusd_value_now": strat_nusd_now,
        "senior_coverage_now": _coverage(strat_nusd_now, sr_now["assets_nusd"]),
        "senior_coverage_at_wipe": _coverage(strat_nusd_wipe, sr_wipe["assets_nusd"]),
        "note": "Coverage = strategy sNUSD × sNUSD share rate / srNUSD totalAssets (NUSD units). After junior write-down, residual collateral is allocated to senior.",
    },
}

out = {
    "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "blocks": {"snapshot": SNAP, "snapshot_ts": ts(SNAP), "freeze": FREEZE_BLOCK, "freeze_ts": ts(FREEZE_BLOCK),
               "reopen_cooldown": REOPEN_COOLDOWN_BLOCK, "reopen_cooldown_ts": ts(REOPEN_COOLDOWN_BLOCK),
               "redeem_open": REDEEM_OPEN_BLOCK, "redeem_open_ts": ts(REDEEM_OPEN_BLOCK), "as_of": head, "as_of_ts": ts(head),
               "first_redeem": min(r["block"] for r in reds), "first_redeem_ts": min(r["ts"] for r in reds), "last_redeem": max(r["block"] for r in reds), "last_redeem_ts": max(r["ts"] for r in reds)},
    "rates": {"snusd_share_rate_snapshot": R_SNAP, "snusd_share_rate_now": R_NOW, "redemption_rate": RATE},
    "supply": {"snapshot": snap_checks, "now": now_checks},
    "totals": {"redeem_events": len(reds), "unique_redeemers": len(by_user), "nusd_redeemed": tot_n, "usdc_paid": tot_u,
               "pct_of_nusd_supply_redeemed": tot_n / snap_checks["nusd_supply"] * 100, "signed_message_wallets": len(signed),
               "signed_not_redeemed": len(signed - set(by_user)),
               "outsider_redeemers": len(outsiders), "outsider_nusd": sum(v["nusd"] for v in outsiders.values()), "outsider_usdc": sum(v["usdc"] for v in outsiders.values()),
               "contract_redeemers": len(contract_redeemers), "contract_redeemer_nusd": sum(v["nusd"] for v in contract_redeemers.values()),
               "usdc_funded_to_reserve": usdc_funded, "usdc_out_of_reserve": usdc_out, "usdc_reserve_now": usdc_reserve_now,
               "snusd_shares_unstaked_since_reopen": sum(v[0] for v in unstk.values()), "snusd_nusd_unstaked_since_reopen": sum(v[1] for v in unstk.values()),
               "snusd_unstakers_since_reopen": len(unstk), "strata_snusd_unstaked": strata_unstaked[0],
               "protocol_contract_value_snapshot": proto_value, "protocol_contracts_snapshot": len(contracts)},
    "summary_all": S_all, "summary_snusd": S_snusd,
    "summary_all_nodust": S_all_nodust, "summary_snusd_nodust": S_snusd_nodust,
    "summary_all_unrestricted": S_all_unrestricted, "summary_snusd_unrestricted": S_snusd_unrestricted,
    # one entry per restriction (an address with both restrictions has two entries), grouped by address, oldest first
    "restricted_list": [dict(address=a, label=RESTRICTED_LABELS.get(a, ""), **e, snapshot_value=snap.get(a, {}).get("value", 0.0))
                        for a, k in sorted(restricted.items(), key=lambda kv: min(e["block"] for e in kv[1].values()))
                        for e in sorted(k.values(), key=lambda e: e["block"])],
    "restricted_meta": {"addresses": len(restricted), "restrictions": sum(len(k) for k in restricted.values()), "lifted": restrictions_lifted,
                        "set_by": sorted({e["set_by"] for k in restricted.values() for e in k.values() if e["set_by"]})},
    "all_wallets": [[r["address"], round(r["value"], 2), round(r["snusd_value"], 2), round(r["redeemed_nusd"], 2), round(r["usdc"], 2), round(r["remaining_value"], 2), r["status"], r["label"], r["restricted"] or ""] for r in sorted(wallets, key=lambda r: -r["value"])],
    "outsiders": sorted([[u, round(v["nusd"], 2), round(v["usdc"], 2)] for u, v in outsiders.items()], key=lambda x: -x[1]),
    "buckets_all": buckets(wallets), "buckets_snusd": buckets(snusd_wallets),
    "daily": daily,
    # hero ring: first-redemption timestamp of every REDEEMED sNUSD snapshot wallet (build.py derives the "latest" dots,
    # i.e. first redemption within 24h of the as-of block)
    "snusd_wallet_first_redeem_ts": sorted(by_user[r["address"]]["first_ts"] for r in snusd_wallets if r["redeemed"]),
    "outsider_sources": outsider_sources,
    "top_wallets": sorted(wallets, key=lambda r: -r["value"])[:100],
    "contracts": contract_tbl,
    "strata": strata,
    "wallet_count_variants": {
        "raw_snusd_direct_holders_incl_contracts": snap_checks["snusd_raw_holders"],
        "raw_nusd_direct_holders_incl_contracts": snap_checks["nusd_raw_holders"],
        "positions_incl_contracts": len(rows), "wallets_excl_protocol_contracts": len(wallets),
        "wallets_eoa": sum(1 for r in wallets if r["label"] == "EOA"), "wallets_7702": sum(1 for r in wallets if r["label"] == "EOA (EIP-7702)"),
        "wallets_safe": sum(1 for r in wallets if r["label"] == "Safe multisig"),
        "snusd_wallets_excl_contracts": len(snusd_wallets)},
}
json.dump(out, open("data/dashboard_data.json", "w"), indent=1)
json.dump({str(k): v for k, v in TS.items()}, open(tsf, "w"))
json.dump(meta, open("data/address_meta.json", "w"), indent=1)
print(json.dumps({k: out[k] for k in ["blocks", "rates", "totals", "summary_all", "summary_snusd", "wallet_count_variants"]}, indent=1))
print(json.dumps({"strata_senior_now": out["strata"]["senior"]["now"], "strata_junior_now": out["strata"]["junior"]["now"], "strata_strategy": {k: out["strata"]["strategy"][k] for k in ("snusd_balance_now","nusd_value_now","senior_coverage_now")}, "junior_wiped": out["strata"]["junior"]["wiped"], "sr_withdrawals_since_wipe": out["strata"]["senior"]["withdrawals_since_wipe"]}, indent=1))
print(json.dumps(out["supply"]["snapshot"], indent=1))
print(out["outsider_sources"][:10])
