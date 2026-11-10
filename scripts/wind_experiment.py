"""Altenthann (and any single-flight project) rank of the truth flight with the wind correction off / on.
usage: python scripts/wind_experiment.py [pid] [variant ...]"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dataclasses import replace
from dopp import project as P, results as R
from dopp.adsb import AdsbLolProvider
from dopp.search import Params, search_single
pid = sys.argv[1] if len(sys.argv) > 1 else "altenthann"
VARIANTS = {
    "off": {}, "era5_full": dict(wind="era5", wind_model="full"), "era5_air": dict(wind="era5", wind_model="air_los"),
    "fit_full": dict(wind="fit", wind_model="full"), "fit_air": dict(wind="fit", wind_model="air_los"),
}
want = sys.argv[2:] or list(VARIANTS)
m = P.meta(pid); ev = [e for e in P.events(pid) if e.get("selected")][0]
st = AdsbLolProvider().load_day(m["day"]); sp = P.spec(pid)
base = Params(region=tuple(m["region"]), single_time_tol_s=800.0, single_dt_sweep_s=800.0, single_coarse_m=0.0)
out = {}
for k in want:
    p = replace(base, **VARIANTS[k]); t = time.time()
    res = search_single(st, sp, ev, ev["line"], m["start_utc"], p, log=lambda s: None)
    s = R.summarize(res, st, 1, tuple(m["truth"][:2]))
    top = [(c["rank"], round(c["total_score"], 3), c["flights"][0]["reg"] or c["flights"][0]["hex"], round(c["dist_to_truth_m"]), c.get("wind_uv")) for c in s["combos"][:6]]
    truth_flight = [c for c in s["combos"] if c["flights"][0]["reg"] == "D-HAIB"]
    out[k] = dict(truth_rank=s.get("truth_rank"), dhaib=(truth_flight[0]["rank"], round(truth_flight[0]["total_score"], 3),
              round(truth_flight[0]["dist_to_truth_m"]), truth_flight[0].get("wind_uv")) if truth_flight else None, top=top, s=round(time.time() - t))
    print(k, json.dumps(out[k], default=str), flush=True)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", f"wind_experiment_{pid}.json")
json.dump(out, open(OUT, "w"), indent=1, default=str)
