import json, os, sys
from rpc import *
OUT = "data"; os.makedirs(OUT, exist_ok=True)
head = int(call("eth_blockNumber", []), 16)
TARGETS = {
  "nusd": ("0xE556ABa6fe6036275Ec1f87eda296BE72C811BCE", 23495846),
  "snusd": ("0x08EFCC2F3e61185D0EA7F8830B3FEc9Bfa2EE313", 23495849),
  "redemption": ("0xB3f07D3392102fC23264a78e2A1A8B6421123828", 25991113),
  "assetreserve": ("0xfEE69fa9C94B9D967390a4F4144E2746603eC1Ed", 25991112),
  "assetlock": ("0x99161ba892ECae335616624c84FAA418F64FF9A6", 23495860),
}
for name,(addr,start) in TARGETS.items():
    fn = f"{OUT}/logs_{name}.json"
    logs = []; frm = start
    if os.path.exists(fn):
        d = json.load(open(fn)); logs = d["logs"]; frm = d["to_block"]+1
    new = get_logs(addr, [], frm, head) if frm <= head else []
    logs += new
    json.dump({"address":addr,"from_block":start,"to_block":head,"logs":logs}, open(fn,"w"))
    print(name, len(logs), "(+%d)"%len(new), flush=True)
print("head", head)

# USDC flows into / out of the redemption AssetReserve (to show funding vs payouts)
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
TR = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
RES = "0x000000000000000000000000fee69fa9c94b9d967390a4f4144e2746603ec1ed"
usdc_in = get_logs(USDC, [TR, None, RES], 25991112, head)
usdc_out = get_logs(USDC, [TR, RES], 25991112, head)
json.dump({"to_block": head, "in": usdc_in, "out": usdc_out}, open(f"{OUT}/logs_usdc_reserve.json", "w"))
print("usdc reserve in", len(usdc_in), "out", len(usdc_out))

# ---------- Strata Neutrl market (topic-filtered; exits settle in sNUSD/NUSD, not Neutrl portal USDC) ----------
# Docs: https://docs.strata.markets/technical-documentation/contracts-details
SRNUSD = "0x65a44528e8868166401eA08b549E19552af589dB"
JRNUSD = "0xFC807058A352b61aEef6A38e2D0fC3990225E772"
STRATA_CDO = "0x7b6c960cf185fb27ECb91c174FAe065978beDd10"
STRATA_START = 24392000   # tranche proxy deployments ~24392064/24392069
WD = "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db"  # Withdraw(address,address,address,uint256,uint256)
DP = "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"  # Deposit(address,address,uint256,uint256)
DSC = "0xf2897f91a349564459bd62561e92b8f21d8d2a78dfeff9491d8a1dfa06a74ef8"  # DepositsStateChanged(address,bool)
WSC = "0x7b4687de2ffa2e710ded8dd0f5b414f7a18f87d6a254ed950046ea657a086623"  # WithdrawalsStateChanged(address,bool)

def pull_filtered(name, addr, topic0_or, start):
    """Incremental eth_getLogs with topic0 OR-filter. Stores only matching events."""
    fn = f"{OUT}/logs_{name}.json"
    logs = []; frm = start
    if os.path.exists(fn):
        d = json.load(open(fn)); logs = d["logs"]; frm = d["to_block"] + 1
    new = get_logs(addr, [topic0_or], frm, head) if frm <= head else []
    logs += new
    json.dump({"address": addr, "from_block": start, "to_block": head, "topics0": topic0_or, "logs": logs}, open(fn, "w"))
    print(name, len(logs), "(+%d)" % len(new), flush=True)

pull_filtered("srnusd", SRNUSD, [WD, DP], STRATA_START)
pull_filtered("jrnusd", JRNUSD, [WD, DP], STRATA_START)
pull_filtered("strata_cdo", STRATA_CDO, [DSC, WSC], STRATA_START)
