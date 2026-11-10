"""Turn search outputs into JSON for the UI; predicted curves and heat maps for a chosen match."""
import math, numpy as np, polars as pl
from .search import score_points, heatmap, Params, _batch_line, corridor_pids, grid_in_box, pid_decode, _g
from dataclasses import replace as _replace
from .physics import model_g_many
from .dem import elevation
from .geo import haversine_m

def flight_label(store, fid):
    m = store.meta(int(fid)); return dict(fid=int(fid), reg=m.get("reg"), type=m.get("type"), hex=m.get("hex"), cls=m.get("cls"))

def summarize(res, store, N, truth=None, top=100, max_points=20000):
    out = {k: res.get(k) for k in ("status", "mode", "gaps", "tolerances", "n_candidates", "n_offset", "n_silence1",
                                   "n_silence2", "n_dense", "n", "elapsed_s", "stats")}
    if res.get("status") != "ok": return out
    combos = res["combos"].head(top)
    rows = []
    for i, r in enumerate(combos.iter_rows(named=True)):
        d = dict(rank=i + 1, lat=r["lat"], lon=r["lon"], total_score=r["total_score"], dt_s=r.get("dt_s"),
                 flights=[flight_label(store, r[f"flightid_{k+1}"]) for k in range(N)],
                 event_scores=[r.get(f"event{k+1}_score") for k in range(N)],
                 f0=[r.get(f"f0_{k+1}") for k in range(N)],
                 cpa_distance_m=[r.get(f"cpa_distance_m_{k+1}") for k in range(N)],
                 cpa_time_utc=[r.get(f"cpa_time_utc_{k+1}") for k in range(N)],
                 seg_speed_mps=[r.get(f"seg_speed_mps_{k+1}") for k in range(N)],
                 silence_penalty=r.get("silence_penalty"), time_diff_s=r.get("time_diff_s"))
        if r.get("wind_u") is not None: d["wind_uv"] = [r["wind_u"], r["wind_v"]]
        if truth: d["dist_to_truth_m"] = float(haversine_m(r["lat"], r["lon"], truth[0], truth[1]))
        rows.append(d)
    out["combos"] = rows
    sc = res["scored"]
    if len(sc) > max_points: sc = sc.head(max_points)
    keys = [f"flightid_{k+1}" for k in range(N)]
    cid = {tuple(int(r[k]) for k in keys): i for i, r in enumerate(res["combos"].select(keys).iter_rows(named=True))}
    out["points"] = dict(lat=sc["lat"].round(6).to_list(), lon=sc["lon"].round(6).to_list(),
                         score=sc["total_score"].round(4).to_list(),
                         ev=[sc[f"event{k+1}_score"].round(4).to_list() for k in range(N)],
                         combo=[cid.get(tuple(int(x) for x in t), -1) for t in sc.select(keys).iter_rows()])
    if truth:
        out["truth"] = dict(lat=truth[0], lon=truth[1])
        tr = [r for r in rows if r["dist_to_truth_m"] < 1000]
        out["truth_rank"] = tr[0]["rank"] if tr else None
    if res.get("mode") == "single":
        s = res["scored"]["total_score"].to_numpy()
        h, e = np.histogram(s, bins=60); out["histogram"] = dict(counts=h.tolist(), edges=e.tolist())
    return out

def _pw(p, row):
    """A 'fit' result carries its own fitted wind per match: re-use it for that match's curves / heat map."""
    if p.wind == "fit" and row.get("wind_uv"): return _replace(p, wind="manual", wind_uv=tuple(row["wind_uv"]))
    return p

def _tf(t, f):
    """Curve samples as JSON lists, dropping samples outside the flight's track coverage (NaN)."""
    t = np.asarray(t, float); f = np.asarray(f, float); ok = np.isfinite(t) & np.isfinite(f)
    return dict(t=t[ok].tolist(), f=f[ok].tolist())

def curves_multi(store, sp, ev, row, p):
    p = _pw(p, row)
    N = len(ev)
    la = np.array([row["lat"]]); lo = np.array([row["lon"]]); el = elevation(la, lo)
    cb = np.array([[f["fid"] for f in row["flights"]]])
    sc, bdt, bf, G, TR, tau = score_points(store, sp, ev, la, lo, el, cb, p)
    return [dict(**_tf(TR[k] + bdt[0], bf[0, k] * G[0, k]), score=float(sc[0, k]), f0=float(bf[0, k]))
            for k in range(N)], float(bdt[0])

def curves_single(store, ev, row, rec_start, p, span=60):
    t = np.arange(ev["t0"] - span, ev["t0"] + span, 0.25)
    dt = row.get("dt_s") or 0.0
    la = np.array([row["lat"]]); lo = np.array([row["lon"]]); el = elevation(la, lo)
    p = _pw(p, row)
    G = _g(store, row["flights"][0]["fid"], p, la, lo, el, (rec_start + t)[None, :])
    return [dict(**_tf(t + dt, row["f0"][0] * G[0]), score=row["total_score"], f0=row["f0"][0])], dt

def heat_multi(store, sp, ev, row, p, half_km=1.5, step_m=25.0):
    p = _pw(p, row)
    h = heatmap(store, sp, ev, [f["fid"] for f in row["flights"]], (row["lat"], row["lon"]), p, half_km, step_m)
    return dict(lat=h["lat"].round(6).tolist(), lon=h["lon"].round(6).tolist(), step_m=step_m,
                layers={k: np.nan_to_num(v, nan=-1e9).round(4).tolist() for k, v in h["layers"].items()})

def heat_single(store, ev, row, rec_start, p, half_km=1.5, step_m=25.0):
    lat0, lon0 = row["lat"], row["lon"]
    dl = half_km * 1000 / 111320.0; dlo = dl / math.cos(math.radians(lat0))
    pids = grid_in_box(lat0 - dl, lat0 + dl, lon0 - dlo, lon0 + dlo, step_m)
    la, lo = pid_decode(pids, step_m); el = elevation(la, lo)
    L = np.asarray(ev["line"], float); tl, fl = L[:, 0], L[:, 1]
    sw = 10.0; dt_c = float(row.get("dt_s") or 0.0)      # around the match's own alignment
    tcurve = np.arange(tl.min() - dt_c - sw - 1, tl.max() - dt_c + sw + 1, 0.25)
    p = _pw(p, row)
    G = _g(store, row["flights"][0]["fid"], p, la, lo, el, np.broadcast_to(rec_start + tcurve, (la.size, tcurve.size)).copy())
    dts = dt_c + np.arange(-sw, sw + 1e-9, 0.5)
    f0rel = 1 + np.arange(-p.f0_sweep_rel, p.f0_sweep_rel + 1e-12, p.f0_step_rel)
    s, _, _ = _batch_line(tl, fl, tcurve, G, dts, ev["f0"] * f0rel,
                          np.arange(-1.0, 1.0001, 0.1), 1 + np.arange(-0.005, 0.0051, 0.001))
    s = np.where(s > -1e17, s, np.nan)
    return dict(lat=la.round(6).tolist(), lon=lo.round(6).tolist(), step_m=step_m,
                layers={"event1": np.nan_to_num(s, nan=-1e9).round(4).tolist()})
