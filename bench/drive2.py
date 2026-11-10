"""Session 8 driver (deadline-aware). Phase A: prep -> audio label -> truth -> prior (+ prior_single) for every clip
(fast, ~1 min each). Phase B: blind (whole-day store, ~8-9 GB, ~20-35 min) one at a time, 30 min hard timeout; a blind run
is not started after DEADLINE - 5 min and is recorded as status 'not_run' (time budget). OOM (rc -9) -> status 'oom'."""
import json, os, sys, subprocess, time, sys
from datetime import datetime
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
B = os.path.join(ROOT, "bench")
DAYS = os.environ.get("DOPP_DAYS", os.path.join(ROOT, "data", "days"))
PY = os.environ.get("PYTHON", sys.executable)
CC = {c["id"]: c for c in json.load(open(f"{B}/candidates.json"))}; C = list(CC)
LAB = f"{B}/audio_labels.json"
TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", 1800))
DEADLINE = datetime.fromisoformat(os.environ.get("BENCH_DEADLINE", "2026-11-09T15:25:00")).timestamp()
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
    d = L(LAB, {})
    if cid in d and d[cid].get("manual"): return
    d[cid] = dict(label=lab, note=note); json.dump(d, open(LAB, "w"), indent=1)
def need(f):
    x = L(f)
    return x is None or (x.get("status") in ("error", "oom", "not_run") and not x.get("error"))
def run_mode(cid, m, log):
    f = f"{B}/runs/{cid}_{m}.json"
    if not need(f): return
    if m == "blind" and time.time() + 300 > DEADLINE:
        json.dump(dict(id=cid, mode=m, status="not_run", reason="time budget (session deadline)"), open(f, "w")); return
    rc, out = sh(["run", cid, m]); log.write(f"{m} {rc} {out}\n"); log.flush()
    if rc == "timeout": json.dump(dict(id=cid, mode=m, status="timeout", elapsed_s=TIMEOUT), open(f, "w"))
    elif rc == -9: json.dump(dict(id=cid, mode=m, status="oom", reason="killed by kernel OOM (rc -9)"), open(f, "w"))
    elif rc != 0: json.dump(dict(id=cid, mode=m, status="error", error=out[-800:] or f"rc {rc}"), open(f, "w"))
    print(cid, m, rc, time.strftime("%H:%M"), flush=True)
def phaseA(cid):
    log = open(f"{B}/runs/{cid}_drive.log", "a")
    if L(f"{B}/dl/{cid}.json", {}).get("status") != "ok": return None
    if not os.path.exists(f"{B}/runs/{cid}_prep.json"):
        rc, out = sh(["prep", cid], 1200); log.write(f"prep {rc} {out}\n"); log.flush()
        if rc != 0: print(cid, "prep fail", rc, flush=True); return None
    pr = L(f"{B}/runs/{cid}_prep.json")
    if pr["screen"]["rms_dbfs_p90"] < -80: label(cid, "no_live_audio", "digital silence (audio track all zeros)"); return None
    lab = L(LAB, {}).get(cid, {})
    if lab.get("label") in ("no_live_audio", "unusable_audio"): return None
    if not lab: label(cid, "live_audio", "auto: non-silent (pending visual screen)")
    if not os.path.exists(f"{B}/runs/{cid}_truthpasses.json"):
        rc, out = sh(["truth", cid], 900); log.write(f"truth {rc} {out}\n"); log.flush()
    if not pr["selected"]: return None
    for m in ["prior"] + (["prior_single"] if len(pr["selected"]) >= 2 else []): run_mode(cid, m, log)
    return log
only = set(sys.argv[1:]); todo = [c for c in C if not only or c in only]
logs = {}
for cid in todo:
    lg = phaseA(cid)
    if lg: logs[cid] = lg
    print("A", cid, "ok" if lg else "skip", time.strftime("%H:%M"), flush=True)
for cid in todo:
    if cid in logs: run_mode(cid, "blind", logs[cid])
print("ALL DONE", time.strftime("%H:%M"), flush=True)
