"""Derive the Livermore Zenodo recorder position (not published) from the ADS-B KML tracks + the
published per-case horizontal offsets L_ADSB_m and heights H_ADSB_above_recorder_m.
Least squares over (lat, lon): sum_i (min horizontal distance from recorder to track_i near its pass - L_i)^2.
Recorder elevation = track altitude at CPA - H_i (median over cases)."""
import sys, re, glob, os, json
import numpy as np, polars as pl
from scipy.optimize import least_squares
from datetime import datetime, timezone
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "examples", "livermore_zenodo_23424278")

def parse_kml(path):
    s = open(path).read()
    when = re.findall(r"<when\s*>([^<]+)</when>", s)
    coords = re.findall(r"<gx:coord\s*>([^<]+)</gx:coord>", s)
    t = np.array([datetime.fromisoformat(w.replace("Z", "+00:00")).timestamp() for w in when])
    c = np.array([[float(x) for x in q.split()] for q in coords])
    n = min(len(t), len(c)); return t[:n], c[:n, 1], c[:n, 0], c[:n, 2]

tab = pl.read_csv(f"{root}/final_results_40_cases.csv")
cases = []
for d in sorted(glob.glob(f"{root}/V_constant_H_constant/*/")):
    name = os.path.basename(d.rstrip("/")); row = tab.filter(pl.col("case") == name).row(0, named=True)
    kml = glob.glob(d + "*.kml")[0]; t, la, lo, alt = parse_kml(kml)
    # densify to 0.5 s
    tt = np.arange(t[0], t[-1], 0.5); _, ix = np.unique(t, return_index=True)
    cases.append(dict(name=name, row=row, t=tt, la=np.interp(tt, t[ix], la[ix]), lo=np.interp(tt, t[ix], lo[ix]),
                      alt=np.interp(tt, t[ix], alt[ix]), t_rec=datetime.fromisoformat(row["received_start_utc"].replace("Z", "+00:00")).timestamp(),
                      dir=d))

def hdist(c, lat, lon, win=180):
    m = np.abs(c["t"] - c["t_rec"]) < win + 120
    x = (c["lo"][m] - lon) * 111320 * np.cos(np.radians(lat)); y = (c["la"][m] - lat) * 111320
    h = np.hypot(x, y); i = np.argmin(h); return h[i], np.flatnonzero(m)[i]

def res(v):
    return [hdist(c, v[0], v[1])[0] - c["row"]["L_ADSB_m"] for c in cases]

# start: grid search around track midpoints
la0 = np.mean([c["la"][np.argmin(np.abs(c["t"] - c["t_rec"]))] for c in cases]); lo0 = np.mean([c["lo"][np.argmin(np.abs(c["t"] - c["t_rec"]))] for c in cases])
best = None
for dla in np.linspace(-0.08, 0.08, 81):
    for dlo in np.linspace(-0.1, 0.1, 101):
        r = np.sum(np.square(res([la0 + dla, lo0 + dlo])))
        if best is None or r < best[0]: best = (r, la0 + dla, lo0 + dlo)
sol = least_squares(res, [best[1], best[2]], x_scale=[1e-3, 1e-3])
lat, lon = sol.x; r = np.array(res(sol.x))
elev = []
for c in cases:
    _, i = hdist(c, lat, lon); elev.append(c["alt"][i] - c["row"]["H_ADSB_above_recorder_m"])
out = dict(lat=float(lat), lon=float(lon), elev_msl_m=float(np.median(elev)), rms_L_residual_m=float(np.sqrt(np.mean(r ** 2))),
           residuals_m={c["name"]: float(x) for c, x in zip(cases, r)}, elev_per_case=dict(zip([c["name"] for c in cases], map(float, elev))))
print(json.dumps(out, indent=1))
json.dump(out, open(f"{root}/derived_recorder_position.json", "w"), indent=1)
