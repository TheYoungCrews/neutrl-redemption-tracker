"""Store the incremental eth_getLogs state in git-friendly form.

data/logs_<name>.json (one big JSON per contract, written/read by pull_logs.py and compute.py) <->
data/segments/<name>/meta.json + data/segments/<name>/<NNNNN>.jsonl  (one log per line, 100k blocks per file).

Old segments never change (logs are append-only), so each hourly commit only touches the newest segment
and git stores it as a small delta.
Usage:  python logstore.py pack     # logs_*.json -> segments (run before committing)
        python logstore.py unpack   # segments -> logs_*.json (run after checkout / git pull)
        python logstore.py verify   # roundtrip check, no writes"""
import json, os, sys, glob
NAMES = ["nusd", "snusd", "redemption", "assetreserve", "assetlock"]   # pull_logs.py TARGETS
SEG = 100_000
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def split(logs):
    segs = {}
    for lg in logs:
        segs.setdefault(int(lg["blockNumber"], 16) // SEG, []).append(lg)
    return segs

def pack(write=True):
    changed = 0
    for n in NAMES:
        src = os.path.join(D, f"logs_{n}.json")
        if not os.path.exists(src): print("skip (missing)", src); continue
        d = json.load(open(src)); out = os.path.join(D, "segments", n); os.makedirs(out, exist_ok=True)
        blocks = [int(l["blockNumber"], 16) for l in d["logs"]]
        assert blocks == sorted(blocks), f"{n}: logs not in block order"
        segs = split(d["logs"])
        files = {f"{k:05d}.jsonl": "".join(json.dumps(l, separators=(",", ":")) + "\n" for l in v) for k, v in segs.items()}
        files["meta.json"] = json.dumps({k: v for k, v in d.items() if k != "logs"} | {"count": len(d["logs"]), "segment_blocks": SEG}, indent=1) + "\n"
        for fn, txt in files.items():
            p = os.path.join(out, fn)
            if os.path.exists(p) and open(p).read() == txt: continue
            if write: open(p, "w").write(txt)
            changed += 1
        for p in glob.glob(os.path.join(out, "*.jsonl")):
            if os.path.basename(p) not in files:
                if write: os.remove(p)
                changed += 1
    print("pack:", changed, "segment file(s) changed")

def load(n):
    out = os.path.join(D, "segments", n)
    meta = json.load(open(os.path.join(out, "meta.json")))
    logs = []
    for p in sorted(glob.glob(os.path.join(out, "*.jsonl"))):
        logs += [json.loads(l) for l in open(p) if l.strip()]
    assert len(logs) == meta["count"], f"{n}: {len(logs)} logs != meta count {meta['count']}"
    return {k: meta[k] for k in meta if k not in ("count", "segment_blocks")} | {"logs": logs}

def unpack():
    for n in NAMES:
        if not os.path.exists(os.path.join(D, "segments", n, "meta.json")): print("skip (no segments)", n); continue
        d = load(n); json.dump(d, open(os.path.join(D, f"logs_{n}.json"), "w"))
        print("unpack:", n, len(d["logs"]), "logs to block", d["to_block"])

def verify():
    for n in NAMES:
        a = json.load(open(os.path.join(D, f"logs_{n}.json"))); b = load(n)
        assert a == b, f"{n}: roundtrip mismatch"
        print("verify ok:", n, len(a["logs"]))

if __name__ == "__main__":
    {"pack": pack, "unpack": unpack, "verify": verify}[sys.argv[1] if len(sys.argv) > 1 else "pack"]()
