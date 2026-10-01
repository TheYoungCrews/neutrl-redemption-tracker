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
