"""Minimal keyless Ethereum JSON-RPC helper (public endpoints, with retry + range fallback)."""
import requests, time, itertools
PRIMARY = "https://mainnet.gateway.tenderly.co"          # allows wide eth_getLogs ranges
FALLBACKS = ["https://eth.drpc.org", "https://ethereum-rpc.publicnode.com"]  # drpc free: <=10k blocks
_s = requests.Session(); _id = itertools.count(1)

def _post(url, method, params):
    r = _s.post(url, json={"jsonrpc":"2.0","id":next(_id),"method":method,"params":params}, timeout=90)
    j = r.json()
    if "error" in j: raise RuntimeError(str(j["error"]))
    return j["result"]

def call(method, params, tries=6):
    urls = [PRIMARY] + FALLBACKS; last = None
    for i in range(tries):
        url = urls[i % len(urls)]
        try: return _post(url, method, params)
        except Exception as e: last = e; time.sleep(1 + i)
    raise RuntimeError(f"{method} failed: {last}")

def _logs_chunk(address, topics, a, b):
    q = [{"address": address, "topics": topics, "fromBlock": hex(a), "toBlock": hex(b)}]
    for i in range(4):
        try: return _post(PRIMARY, "eth_getLogs", q)
        except Exception as e: last = e; time.sleep(1 + 2*i)
    # fallback: 10k-block pieces on drpc / publicnode
    out = []
    for s in range(a, b+1, 10000):
        e = min(b, s+9999); qq = [{"address": address, "topics": topics, "fromBlock": hex(s), "toBlock": hex(e)}]
        for i in range(10):
            try: out += _post(FALLBACKS[i % 2], "eth_getLogs", qq); break
            except Exception as ex:
                if i == 9: raise RuntimeError(f"getLogs {s}-{e} failed: {ex} (primary: {last})")
                time.sleep(1 + i)
    return out

def get_logs(address, topics, start, end, step=50000):
    out = []
    for a in range(start, end+1, step):
        out += _logs_chunk(address, topics, a, min(end, a+step-1))
    return out

def block(n):
    return call("eth_getBlockByNumber", [hex(n) if isinstance(n, int) else n, False])

def eth_call(to, data, blk="latest"):
    return call("eth_call", [{"to": to, "data": data}, hex(blk) if isinstance(blk, int) else blk])

def block_at_ts(ts, lo=20000000, hi=None):
    if hi is None: hi = int(call("eth_blockNumber", []), 16)
    while lo < hi:
        mid = (lo+hi)//2
        if int(block(mid)["timestamp"], 16) < ts: lo = mid+1
        else: hi = mid
    return lo
