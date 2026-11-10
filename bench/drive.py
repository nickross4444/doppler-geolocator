"""Poll downloads; for each downloaded clip: prep -> auto audio label (digital silence) -> truth diagnostic -> blind, prior
(+ prior_single when the auto policy picked multi). Each run is a subprocess with a hard 30 min timeout. 2 clips at a time."""
import json, os, sys, subprocess, time, sys
from concurrent.futures import ThreadPoolExecutor
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
B = os.path.join(ROOT, "bench")
DAYS = os.environ.get("DOPP_DAYS", os.path.join(ROOT, "data", "days"))
PY = os.environ.get("PYTHON", sys.executable)
C = [c["id"] for c in json.load(open(f"{B}/candidates.json"))]
LAB = f"{B}/audio_labels.json"
TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", 1800))
def L(p, d=None): return json.load(open(p)) if os.path.exists(p) else d
def sh(args, to=TIMEOUT):
    t = time.time()
    try:
        r = subprocess.run([PY, f"{B}/run_bench.py", *args], capture_output=True, text=True, timeout=to)
        out = "\n".join(l for l in r.stdout.splitlines() if "Warning" not in l)[-600:]
        return r.returncode, out + ("" if r.returncode == 0 else r.stderr[-1500:])
    except subprocess.TimeoutExpired:
        return "timeout", f"timeout after {time.time()-t:.0f}s"
def label(cid, lab, note):
    d = L(LAB, {});
    if cid in d and d[cid].get("manual"): return
    d[cid] = dict(label=lab, note=note); json.dump(d, open(LAB, "w"), indent=1)
def process(cid):
    log = open(f"{B}/runs/{cid}_drive.log", "a")
    if not os.path.exists(f"{B}/runs/{cid}_prep.json"):
        rc, out = sh(["prep", cid], 1200); log.write(f"prep {rc} {out}\n"); log.flush()
        if rc != 0: return
        if not L(f'{B}/runs/{cid}_prep.json')['screen']['rms_dbfs_p90'] < -80 and not os.path.exists(os.path.join(DAYS, [c for c in json.load(open(f"{B}/candidates.json")) if c["id"]==cid][0]["utc_start"][:10], "flights.parquet")): done.discard(cid); return
    pr = L(f"{B}/runs/{cid}_prep.json"); s = pr["screen"]
    if s["rms_dbfs_p90"] < -80: label(cid, "no_live_audio", "digital silence (audio track all zeros)"); return
    lab = L(LAB, {}).get(cid, {})
    if lab.get("label") in ("no_live_audio", "unusable_audio"): return
    if not lab: label(cid, "live_audio", "auto: non-silent (pending visual screen)")
    if not os.path.exists(f"{B}/runs/{cid}_truthpasses.json"):
        rc, out = sh(["truth", cid], 900); log.write(f"truth {rc} {out}\n"); log.flush()
    if not pr["selected"]: return
    modes = ["blind", "prior"] + (["prior_single"] if len(pr["selected"]) >= 2 else [])
    for m in modes:
        f = f"{B}/runs/{cid}_{m}.json"
        if os.path.exists(f):
            x = L(f, {})
            if not (x.get("status") == "error" and not x.get("error")): continue   # rerun OOM-killed (-9, empty stderr)
        rc, out = sh(["run", cid, m]); log.write(f"{m} {rc} {out}\n"); log.flush()
        if rc == "timeout":
            json.dump(dict(id=cid, mode=m, status="timeout", elapsed_s=TIMEOUT), open(f, "w"))
        elif rc != 0:
            json.dump(dict(id=cid, mode=m, status="error", error=out[-800:]), open(f, "w"))
    print("done", cid, flush=True)
done = set(); only = set(sys.argv[1:])
with ThreadPoolExecutor(int(os.environ.get("BENCH_WORKERS", 1))) as ex:
    while True:
        pending = []
        for cid in C:
            if only and cid not in only: continue
            d = L(f"{B}/dl/{cid}.json")
            if d is None: pending.append(cid); continue
            if cid in done or d["status"] != "ok": continue
            cc = [c for c in json.load(open(f"{B}/candidates.json")) if c["id"] == cid][0]
            pr = L(f"{B}/runs/{cid}_prep.json")
            silent = pr is not None and pr["screen"]["rms_dbfs_p90"] < -80
            if pr is not None and not silent and not os.path.exists(os.path.join(DAYS, cc["utc_start"][:10], "flights.parquet")): pending.append(cid); continue
            done.add(cid); ex.submit(process, cid)
        if not pending: break
        time.sleep(30)
