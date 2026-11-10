"""Harmonic-aware fit vs the single-line fit on every preset (dopp/harmonics.py; optional, default off).
For each preset result (non-CONUS tags): the selected events' (v, d, t0) from the single-line fit and from the joint comb fit,
the truth flight's ADS-B speed, and the search re-run with the comb-refit events (truth rank / distance / score).
usage: python scripts/harmonic_experiment.py [--no-search] [pid ...]   -> data/harmonic_experiment.json"""
import sys, os, json, glob, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dopp import project as P, results as R
from dopp.adsb import AdsbLolProvider
from dopp.search import Params, search_single, search_multi
from dopp.harmonics import harmonic_refit, hop_check, asymptote_ratio

args = [a for a in sys.argv[1:] if not a.startswith("--")]; NOSEARCH = "--no-search" in sys.argv
PIDS = args or ["altenthann", "oysterbay", "livermore_n394fx_r20", "livermore_n4040_r12", "livermore_n413ak_r49",
                "livermore_n788bm_r03", "livermore_n916kt_r22", "ithaca_feederwatch", "venice_beach"]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "harmonic_experiment.json")
out = json.load(open(OUT)) if os.path.exists(OUT) else {}
STORES = {}

def top(s, n=5):
    return [(c["rank"], round(c["total_score"], 3), "+".join((f["reg"] or f["hex"]) for f in c["flights"]), round(c.get("dist_to_truth_m", -1)))
            for c in (s.get("combos") or [])[:n]]

for pid in PIDS:
    m = P.meta(pid); sp = P.spec(pid); truth = tuple(m["truth"][:2])
    for f in sorted(glob.glob(os.path.join(P.pdir(pid), "results", "*.json"))):
        if "_" in os.path.basename(f): continue
        d = P.load_json(f)
        if not d.get("preset") or "conus" in (d.get("tag") or "") or d.get("status") != "done": continue
        tag = d["tag"]; key = f"{pid}:{tag}"; t = time.time()
        s0 = d["summary"]; tr = [c for c in s0.get("combos") or [] if c.get("dist_to_truth_m", 1e9) < 1000]
        ev0 = d["events"]; ev1 = [harmonic_refit(sp, e) for e in ev0]
        rows = []
        for k, (a, b) in enumerate(zip(ev0, ev1)):
            c = b.get("comb") or {}
            rows.append(dict(event=a.get("id"), t0=round(a["t0"], 2), f0=round(a["f0"], 2),
                             single=dict(v=round(a["v"], 2), d=round(a["d"]), t0=round(a["t0"], 2)),
                             comb=dict(v=round(b["v"], 2), d=round(b["d"]), t0=round(b["t0"], 2), F=round(c.get("F", 0), 3),
                                       k_line=c.get("k_line"), n_harm=len(c.get("used", [])), v_sd=round(c.get("v_sd", 0), 2)) if c else None,
                             hop=hop_check(a["line"], c["F"], c["v"], c["d"], c["t0"]) if c else None, comb_used=c.get("used"),
                             asym=asymptote_ratio(a["line"], a["t0"]),
                             truth_flight_speed=round(tr[0]["seg_speed_mps"][k], 2) if tr else None,
                             truth_flight=(tr[0]["flights"][k]["reg"] if tr else None)))
        r = dict(tag=tag, mode=d["mode"], events=rows, before=dict(truth_rank=s0.get("truth_rank"), top=top(s0)))
        r["changed"] = any(b.get("comb") for b in ev1)
        if not r["changed"]: r["after"] = dict(r["before"], note="no comb found for any event: identical inputs, not re-run")
        if not NOSEARCH and r["changed"]:
            day = d["day"]; st = STORES.get(day) or STORES.setdefault(day, AdsbLolProvider().load_day(day))
            pp = {k: (tuple(v) if isinstance(v, list) else v) for k, v in d["params"].items()}
            p = Params(**pp)
            if d["mode"] == "single":
                res = search_single(st, sp, ev1[0], ev1[0]["line"], d["rec_start"], p, log=lambda s: None)
            else:
                res = search_multi(st, sp, ev1, p, log=lambda s: None)
            s1 = R.summarize(res, st, len(ev1), truth)
            r["after"] = dict(truth_rank=s1.get("truth_rank"), top=top(s1))
        r["s"] = round(time.time() - t)
        out[key] = r; json.dump(out, open(OUT, "w"), indent=1, default=str)
        print(key, json.dumps({k: r[k] for k in r if k != "events"}, default=str), flush=True)
        for x in rows: print("    ", json.dumps({k: x[k] for k in ("event", "single", "comb", "truth_flight", "truth_flight_speed")}, default=str), "hop", (x["hop"] or {}).get("hop"), flush=True)
