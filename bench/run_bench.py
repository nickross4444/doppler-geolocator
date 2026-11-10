"""Outdoor-video benchmark runner (Doppler geolocator).
  prep <id>          project from bench/audio/<id>.m4a, audio screen stats, auto event detection, overview PNG
  run <id> <mode>    mode = blind | prior | prior_single | blind_wind | prior_wind ; writes bench/runs/<id>_<mode>.json
Ground truth (location, time) is used ONLY for scoring; 'blind' = date only (whole day store: CONUS or Europe),
'prior' = recording start from the stream metadata +- prior_uncertainty_s (location still unknown).
Automatic policy (no human in the loop): events = auto-detect (as the presets: max_events=40, half_window 40 s),
primary passes only, ranked by confidence; >=2 events with t0 at least 60 s apart -> multi-event search on the two
most confident, else single-flight on the most confident (needs the time prior)."""
import sys, os, json, time, math, traceback
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from datetime import datetime
from dataclasses import asdict
import numpy as np
from dopp import project as P
from dopp.adsb import AdsbLolProvider
from dopp.search import Params, search_multi, search_single
from dopp import results as R
from dopp.geo import haversine_m

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
B = os.path.join(ROOT, "bench")
DAYS = os.environ.get("DOPP_DAYS", os.path.join(ROOT, "data", "days"))
CANDS = {c["id"]: c for c in json.load(open(f"{B}/candidates.json"))}
EU = {"notting_hill_gate_20261016", "stives_harbour_20260802", "berchtesgaden_markt_20260418", "krakow_rynek_20261108", "moosburg_20240421"}
OCMD = {k: (CANDS[k]["lat"], CANDS[k]["lon"]) for k in CANDS if k.startswith("ocmd_")}

def ts(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
def pid_of(cid): return "bench_" + cid
def day_of(c): return c["utc_start"][:10]

def screen_audio(wav):
    from dopp.audio import read_wav; sr, x = read_wav(wav)
    n = sr; k = x.size // n; X = x[:k * n].reshape(k, n)
    rms = np.sqrt((X ** 2).mean(1)) + 1e-12; db = 20 * np.log10(rms)
    peak = 20 * np.log10(np.abs(x).max() + 1e-12)
    # spectral shape per frame: low-band (<150 Hz) share (wind/rumble) and flatness
    S = np.abs(np.fft.rfft(X * np.hanning(n), axis=1)) ** 2 + 1e-18; f = np.fft.rfftfreq(n, 1 / sr)
    flat = np.exp(np.log(S[:, 20:3000]).mean(1)) / S[:, 20:3000].mean(1)
    low = S[:, (f >= 20) & (f < 150)].sum(1) / S[:, (f >= 20) & (f < 3500)].sum(1)
    return dict(duration_s=float(x.size / sr), rms_dbfs_median=float(np.median(db)), rms_dbfs_p10=float(np.percentile(db, 10)),
                rms_dbfs_p90=float(np.percentile(db, 90)), peak_dbfs=float(peak), frac_silent_lt_m70=float((db < -70).mean()),
                frac_silent_lt_m60=float((db < -60).mean()), flatness_median=float(np.median(flat)), lowband_share_median=float(np.median(low)))

def overview_png(pid, out, t0=None, t1=None, f1=1200, w=1600, h=420):
    from PIL import Image
    sp = P.spec(pid); t0 = sp.t[0] if t0 is None else t0; t1 = sp.t[-1] if t1 is None else t1
    i0, i1 = np.searchsorted(sp.t, [t0, t1]); j1 = np.searchsorted(sp.f, f1)
    A = sp.db[i0:i1, :j1]; k = max(1, A.shape[0] // w)
    A = A[: (A.shape[0] // k) * k].reshape(-1, k, A.shape[1]).mean(1)
    lo, hi = np.percentile(A, 2), np.percentile(A, 99.5)
    U = np.clip((A - lo) / (hi - lo + 1e-9), 0, 1)
    import server_lut
    img = Image.fromarray(server_lut.LUT[(U.T[::-1] * 255).astype(np.uint8)]).resize((w, h))
    img.save(out)

def prep(cid):
    c = CANDS[cid]; pid = pid_of(cid); src = f"{B}/audio/{cid}.m4a"
    if not os.path.exists(os.path.join(P.pdir(pid), "meta.json")):
        P.create_project(src, f"[bench] {c['location_name']} ({day_of(c)})", pid=pid, start_utc=ts(c["utc_start"]),
                         tz="UTC", day=day_of(c),
                         extra=dict(truth=[c["lat"], c["lon"]], bench=True, truth_radius_m=c["location_uncertainty_m"]))
    scr = screen_audio(os.path.join(P.pdir(pid), "audio.wav"))
    t = time.time(); sp = P.spec(pid)
    ev = P.auto_detect(sp, max_events=40, half_window_s=40.0)
    for i, e in enumerate(ev): e["id"] = i + 1; e["selected"] = False
    sel = select_events(ev)
    for e in ev: e["selected"] = e["id"] in [s["id"] for s in sel]
    P.set_events(pid, ev)
    out = dict(id=cid, pid=pid, screen=scr, detect_s=round(time.time() - t, 1), n_events=len(ev),
               n_primary=sum(1 for e in ev if e.get("primary")),
               events=[dict(id=e["id"], t0=e["t0"], f0=e["f0"], v=e["v"], d=e["d"], conf=e["conf"], rms=e.get("fit_rms_hz"),
                            primary=e.get("primary"), n_line=len(e.get("line", []))) for e in ev],
               selected=[e["id"] for e in sel])
    overview_png(pid, f"{B}/screen/{cid}_overview.png")
    P.save_json(f"{B}/runs/{cid}_prep.json", out)
    print(json.dumps({k: out[k] for k in ("id", "n_events", "n_primary", "selected", "detect_s")}), json.dumps(scr))
    return out

def select_events(ev):
    pr = sorted([e for e in ev if e.get("primary") and e.get("v_min") is not None], key=lambda e: -e["conf"])
    if not pr: return []
    sel = [pr[0]]
    for e in pr[1:]:
        if all(abs(e["t0"] - s["t0"]) >= 60 for s in sel): sel.append(e); break
    return sel

def score(summary, c, mode):
    truth = (c["lat"], c["lon"]); rad = c["location_uncertainty_m"] + 500.0
    rows = summary.get("combos") or []
    out = dict(n_rows=len(rows), hit_radius_m=rad)
    if not rows: return out
    d = [float(haversine_m(r["lat"], r["lon"], *truth)) for r in rows]
    tr = [i + 1 for i, x in enumerate(d) if x <= rad]
    s1 = rows[0]["total_score"]; s2 = rows[1]["total_score"] if len(rows) > 1 else None
    out.update(truth_rank=tr[0] if tr else None, top1_dist_m=d[0], top1_lat=rows[0]["lat"], top1_lon=rows[0]["lon"],
               min_dist_top10_m=min(d[:10]), s1=s1, s2=s2,
               margin=(None if s2 is None else (s1 - s2) / abs(s1) if s1 else None),
               truth_score=rows[tr[0] - 1]["total_score"] if tr else None,
               top1_flights=[f["reg"] or f["hex"] for f in rows[0]["flights"]],
               top1_types=[f["type"] for f in rows[0]["flights"]],
               truth_flights=[f["reg"] or f["hex"] for f in rows[tr[0] - 1]["flights"]] if tr else None)
    if c["id"] in OCMD:   # three OCMD cams within ~1.5 km: does top-1 land on a *different* OCMD cam?
        dd = {k: float(haversine_m(rows[0]["lat"], rows[0]["lon"], *v)) for k, v in OCMD.items()}
        out["ocmd_nearest_cam"] = min(dd, key=dd.get); out["ocmd_dists_m"] = dd
    return out

def run(cid, mode):
    c = CANDS[cid]; pid = pid_of(cid); m = P.meta(pid); evs = P.events(pid)
    sel = sorted([e for e in evs if e.get("selected")], key=lambda e: e["t0"])
    prior = float(c["prior_uncertainty_s"])
    rec = dict(id=cid, mode=mode, day=day_of(c), started=time.time(), n_selected=len(sel))
    wind = "era5" if mode.endswith("_wind") else "off"
    base = mode.replace("_wind", "")
    p = Params(wind=wind)
    if base == "blind":
        if len(sel) < 2: rec.update(status="not_runnable", reason="1 event: single-flight needs a time prior"); return save(rec)
        smode = "multi"
    elif base == "prior":
        smode = "multi" if len(sel) >= 2 else "single"
    elif base == "prior_single":
        smode = "single"; sel = sorted([e for e in evs if e.get("selected")], key=lambda e: -e["conf"])[:1]
    if not sel: rec.update(status="no_events"); return save(rec)
    if smode == "multi" and base == "prior":
        tref = m["start_utc"] + sel[0]["t0"]; p.time_window = (tref - prior, tref + prior)
    if smode == "single":
        sel = sorted(sel, key=lambda e: -e["conf"])[:1]
        p.single_time_tol_s = max(60.0, prior); p.single_dt_sweep_s = max(60.0, prior)
    rec.update(search_mode=smode, events=[dict(id=e["id"], t0=e["t0"], f0=e["f0"], v=e["v"], d=e["d"], conf=e["conf"]) for e in sel],
               params=asdict(p))
    store = AdsbLolProvider().load_day(day_of(c))
    logs = []
    t = time.time()
    if smode == "multi": res = search_multi(store, P.spec(pid), sel, p, log=logs.append); res["mode"] = "multi"; N = len(sel)
    else:
        e = sel[0]; res = search_single(store, P.spec(pid), e, e["line"], m["start_utc"], p, log=logs.append); res["mode"] = "single"; N = 1
    el = time.time() - t
    summ = R.summarize(res, store, N, (c["lat"], c["lon"]))
    rec.update(status=res.get("status"), elapsed_s=el, log=logs, sc=score(summ, c, mode),
               pipeline={k: summ.get(k) for k in ("n_candidates", "n_offset", "n_silence1", "n_silence2", "n_dense", "n")},
               top5=[dict(rank=r["rank"], lat=r["lat"], lon=r["lon"], score=r["total_score"], dist_m=r.get("dist_to_truth_m"),
                          flights=[(f["reg"] or f["hex"], f["type"]) for f in r["flights"]]) for r in (summ.get("combos") or [])[:5]])
    # also store in the project like the server does, so the app/viewer can open it
    jid = f"b{cid[:4]}{mode[:2]}{int(time.time()) % 100000}".replace("_", "")
    job = dict(id=jid, pid=pid, mode=smode, day=day_of(c), status="done" if res.get("status") == "ok" else "empty", progress=1.0,
               stage=res.get("status"), log=logs, events=sel, params=asdict(p), created=time.time(), rec_start=m["start_utc"],
               summary=summ, tag=f"bench_{mode}")
    d = os.path.join(P.pdir(pid), "results"); os.makedirs(d, exist_ok=True); P.save_json(os.path.join(d, jid + ".json"), job)
    rec["job_id"] = jid
    return save(rec)

def save(rec):
    P.save_json(f"{B}/runs/{rec['id']}_{rec['mode']}.json", rec)
    s = rec.get("sc") or {}
    print(rec["id"], rec["mode"], rec.get("status"), rec.get("reason", ""), "rank", s.get("truth_rank"), "top1_km",
          None if s.get("top1_dist_m") is None else round(s["top1_dist_m"] / 1000, 2), "t", round(rec.get("elapsed_s") or 0), flush=True)
    return rec

def truth_passes(cid, max_km=8.0):
    """Ground-truth diagnostic (NOT used by the search): ADS-B passes near the true camera during the segment.
    Heard time = delayed CPA (tau) relative to the recording start; then match the auto events to them."""
    from dopp.dem import elevation
    from dopp.search import cpa_many, _track_window
    c = CANDS[cid]; pid = pid_of(cid); m = P.meta(pid); st = AdsbLolProvider().load_day(day_of(c))
    rec0 = m["start_utc"]; dur = m["duration_s"]
    la, lo = np.array([c["lat"]]), np.array([c["lon"]]); el = elevation(la, lo)
    fids = st.fids_active(rec0 - 300, rec0 + dur + 300, ("jet", "prop", "rotor", "unknown"))
    out = []
    for f in fids:
        tr = st.track(int(f)); k = (tr["t"] >= rec0 - 300) & (tr["t"] <= rec0 + dur + 300) & tr["valid"] & np.isfinite(tr["lat"])
        if k.sum() < 5: continue
        hz = haversine_m(c["lat"], c["lon"], tr["lat"][k], tr["lon"][k]); vz = tr["alt_m"][k] - el[0]
        d = np.sqrt(hz ** 2 + vz ** 2); t = tr["t"][k]
        # local minima below max_km (an aircraft can pass several times)
        idx = [i for i in range(len(d)) if d[i] < max_km * 1000 and d[i] == d[max(0, i - 90):i + 91].min()]
        for i in idx:
            th = t[i] + d[i] / 343.0 - rec0
            if -30 <= th <= dur + 30:
                mm = st.meta(int(f))
                out.append(dict(fid=int(f), reg=mm["reg"] or mm["hex"], type=mm["type"], cls=mm["cls"], t_heard=float(th),
                                slant_km=float(d[i] / 1000), alt_m=float(tr["alt_m"][k][i]), gs_mps=float(tr["gs"][k][i])))
    out.sort(key=lambda r: r["t_heard"])
    ev = P.events(pid); match = []
    for e in ev:
        if not e.get("primary"): continue
        best = None
        for r in out:
            dt = e["t0"] - r["t_heard"]
            if abs(dt) <= 20 and (best is None or abs(dt) < abs(best[1])): best = (r, dt)
        match.append(dict(id=e["id"], t0=e["t0"], f0=e["f0"], v=e["v"], d=e["d"], conf=e["conf"], selected=e.get("selected"),
                          pass_reg=best[0]["reg"] if best else None, pass_cls=best[0]["cls"] if best else None,
                          pass_slant_km=best[0]["slant_km"] if best else None, pass_gs=best[0]["gs_mps"] if best else None,
                          dt=best[1] if best else None))
    rr = dict(id=cid, passes=out, n_nonjet_3km=sum(1 for r in out if r["cls"] != "jet" and r["slant_km"] < 3),
              n_nonjet_5km=sum(1 for r in out if r["cls"] != "jet" and r["slant_km"] < 5),
              n_jet_5km=sum(1 for r in out if r["cls"] == "jet" and r["slant_km"] < 5), event_matches=match)
    P.save_json(f"{B}/runs/{cid}_truthpasses.json", rr)
    print(cid, "nonjet<3km", rr["n_nonjet_3km"], "nonjet<5km", rr["n_nonjet_5km"], "jet<5km", rr["n_jet_5km"],
          "selected matched:", [(x["id"], x["pass_reg"], x["pass_cls"], x["pass_slant_km"] and round(x["pass_slant_km"], 1)) for x in match if x["selected"]],
          "primary matched:", sum(1 for x in match if x["pass_reg"]), "/", len(match))
    return rr

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "prep": prep(sys.argv[2])
    elif cmd == "run": run(sys.argv[2], sys.argv[3])
    elif cmd == "truth": truth_passes(sys.argv[2])
