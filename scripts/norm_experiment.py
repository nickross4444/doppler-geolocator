"""Oyster Bay CONUS margin under different score normalisations / aggregations (task: SPEC Q2/Q7).
usage: python scripts/norm_experiment.py [variant ...]"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataclasses import replace
import numpy as np
from dopp import project as P, results as R
from dopp.adsb import AdsbLolProvider
from dopp.search import Params, search_multi
V = {"z_mean": {}, "z_sum": dict(score_agg="sum"), "db_mean": dict(score_norm="db_med"), "db_sum": dict(score_norm="db_med", score_agg="sum"),
     "z_mean_h3": dict(harmonics=3), "db_mean_h3": dict(score_norm="db_med", harmonics=3), "z_mean_sj": dict(silence_count_jets=True),
     "f3": dict(f0_sweep_rel=0.03), "tol5": dict(tol_sigma_k=0.0), "f3_tol5": dict(f0_sweep_rel=0.03, tol_sigma_k=0.0), "f2": dict(f0_sweep_rel=0.02), "time300": {}, "time900": {}}
want = sys.argv[1:] or list(V)
pid = "oysterbay"; m = P.meta(pid)
ev = sorted([e for e in P.events(pid) if e.get("label", "").endswith("(video ranges)")], key=lambda e: e["t0"])
st = AdsbLolProvider().load_day(m["day"]); sp = P.spec(pid)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "norm_experiment.json")
out = json.load(open(OUT)) if os.path.exists(OUT) else {}
for k in want:
    t = time.time(); p = replace(Params(), **V[k])
    if k.startswith("time"):     # known recording start (filename clock) +- N s, as the server's use_start_time option
        w = float(k[4:]); tref = m["start_utc"] + ev[0]["t0"]; p = replace(p, time_window=(tref - w, tref + w))
    res = search_multi(st, sp, ev, p, log=lambda s: None)
    s = R.summarize(res, st, 2, tuple(m["truth"]))
    c = s["combos"]; tot = res["scored"]["total_score"].to_numpy()
    s1, s2 = c[0]["total_score"], c[1]["total_score"]
    r = dict(truth_rank=s.get("truth_rank"), s1=round(s1, 3), s2=round(s2, 3), margin_pct=round(100 * (s1 - s2) / abs(s1), 2),
             gap_over_std=round((s1 - s2) / tot.std(), 3), gap_over_p50spread=round((s1 - s2) / (s1 - np.median(tot)), 3),
             top=[(x["rank"], round(x["total_score"], 3), [round(v, 2) for v in x["event_scores"]], "+".join(f["reg"] or f["hex"] for f in x["flights"]),
                   round(x["dist_to_truth_m"] / 1000, 1)) for x in c[:4]], n=len(tot), s=round(time.time() - t))
    out[k] = r; print(k, json.dumps(r), flush=True)
    json.dump(out, open(OUT, "w"), indent=1)
