"""Build the preloaded examples (projects + precomputed searches) against a running server.
usage: python scripts/build_presets.py [--base http://localhost:8765] [--only oyster|oyster_time|livermore|altenthann|feederwatch|venice|altenthann_wind] [--skip-conus]"""
import sys, os, json, time, argparse, glob
import urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dopp import project as P
from datetime import datetime
import polars as pl

ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://localhost:8765"); ap.add_argument("--only", default="")
ap.add_argument("--skip-conus", action="store_true"); ap.add_argument("--phase", default="", help="regional | conus (conus reuses the saved events)"); ap.add_argument("--rebuild", action="store_true")
A = ap.parse_args()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.environ.get("DOPP_EXAMPLES", os.path.join(ROOT, "examples"))

def call(path, body=None):
    req = urllib.request.Request(A.base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with urllib.request.urlopen(req, timeout=7200) as r: return json.loads(r.read())

def wait(jid, tag="", heat=True):
    while True:
        j = call(f"/api/jobs/{jid}")
        if j["status"] not in ("queued", "running"): break
        time.sleep(3)
    j = call(f"/api/jobs/{jid}?full=1"); s = j.get("summary") or {}
    print(f"   job {jid}: {j['status']} truth_rank={s.get('truth_rank')} elapsed={s.get('elapsed_s')}")
    for r in (s.get("combos") or [])[:5]:
        print("     ", r["rank"], round(r["total_score"], 3), [f["reg"] for f in r["flights"]], round(r.get("dist_to_truth_m", -1)), flush=True)
    # mark as preset
    f = os.path.join(P.pdir(j["pid"]), "results", jid + ".json"); d = P.load_json(f); d["preset"] = True; d["tag"] = tag
    P.save_json(f, d)
    if j["status"] == "done" and heat:       # precompute curves + heat maps so the preset opens instantly
        n = len(s.get("combos") or [])
        for rk in range(1, min(3, n) + 1):
            call(f"/api/jobs/{jid}/curves?rank={rk}"); call(f"/api/jobs/{jid}/heatmap?rank={rk}")
    return j

def drop_old(pid, tag):
    """Remove earlier preset results with the same tag (and untagged leftovers)."""
    d = os.path.join(P.pdir(pid), "results")
    if not os.path.isdir(d): return
    for f in list(os.listdir(d)):
        if f.endswith(".json") and "_" not in f[:-5]:
            j = P.load_json(os.path.join(d, f))
            if j and j.get("tag") in (tag, None):
                for g in os.listdir(d):
                    if g.startswith(j["id"]): os.remove(os.path.join(d, g))

def run(pid, tag, body, heat=True):
    drop_old(pid, tag); print(f" [{pid}] {tag}", flush=True)
    return wait(call(f"/api/projects/{pid}/search", body)["job"], tag, heat)

REGIONAL = A.phase in ("", "regional"); CONUS = A.phase in ("", "conus") and not A.skip_conus


def ensure(pid, src, name, **kw):
    if os.path.exists(os.path.join(P.pdir(pid), "meta.json")) and not A.rebuild: return P.meta(pid)
    return P.create_project(src, name, pid=pid, **kw)

def detect(pid, **kw):
    sp = P.spec(pid); ev = P.auto_detect(sp, **kw)
    for i, e in enumerate(ev): e["id"] = i + 1; e["selected"] = False
    P.set_events(pid, ev); return ev

def ts(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

# ------------------------------------------------------------------ 1. Oyster Bay (video description example)
if A.only in ("", "oyster"):
    pid = "oysterbay"; REG = [40.3, 41.4, -74.5, -72.5]
    # clip = minutes 60-120 of the 3 h recording; filename clock 19:18:04Z (+60 min). That clock is ~34 s off
    # (found by single-flight mode); we keep the as-given time so the tool has to recover it.
    m = ensure(pid, f"{EX}/example1_audio_mono8k.wav", "Oyster Bay, NY (video example, 60-120 min)",
               start_utc=ts("2026-09-08T20:18:04Z"), tz="America/New_York", day="2026-09-08",
               extra=dict(preset=True, order=1, truth=[40.876466, -73.524951], region=REG,
                          note="truth = author's on-screen top match; date 2026-09-08 reproduces it"))
    if REGIONAL:
        ev = detect(pid, max_events=40)
        a1 = min(ev, key=lambda e: abs(e["t0"] - 2155)); a2 = min(ev, key=lambda e: abs(e["t0"] - 3076))
        ef = min(ev, key=lambda e: abs(e["t0"] - 1281) + (0 if abs(e["f0"] - 382) < 30 else 1e6))
        ef["label"] = "faint jet (single-flight)"
        print("oyster auto events:", [(e["id"], round(e["t0"], 1), round(e["f0"], 1), round(e["conf"], 1)) for e in (a1, a2, ef)], flush=True)
        # 'manual' events as in the video: LSQ fit of the four sliders to the ridge (here the auto ridge trace), then the
        # author's own generous MIN/MAX ranges from the measurement slide, BEST = midpoint [KF 09m35s, 12m42s]
        sp = P.spec(pid); man = []
        for a, (vr, dr, lab) in ((a1, ((130, 154), (4300, 7800), "event 1 (video ranges)")),
                                 (a2, ((88, 110), (1390, 4690), "event 2 (video ranges)"))):
            e = P.fit_trace(sp, a["line"], init=[a["f0"], a["v"], a["d"], a["t0"]])
            e.update(v_min=vr[0], v_max=vr[1], d_min=dr[0], d_max=dr[1], v_best=sum(vr) / 2, d_best=sum(dr) / 2,
                     source="manual", label=lab, selected=True, id=P._next_id(ev + man))
            man.append(e)
        ev = man + ev; P.set_events(pid, ev)
        print("manual:", [(e["id"], round(e["t0"], 2), round(e["f0"], 2), round(e["v"], 1), round(e["d"])) for e in man],
              "gap", round(man[1]["t0"] - man[0]["t0"], 2), flush=True)
        P.set_meta(pid, view=dict(t0=man[0]["t0"] - 200, t1=man[1]["t0"] + 200, f0=60, f1=450), default_mode="multi", region=REG, default_tag="multi_region")
        ids = [e["id"] for e in man]
        run(pid, "multi_region", dict(mode="multi", event_ids=ids, params=dict(region=REG)))
        run(pid, "multi_region_auto", dict(mode="multi", event_ids=[a1["id"], a2["id"]], params=dict(region=REG)))
        run(pid, "single_region", dict(mode="single", event_ids=[ef["id"]], params=dict(include_jets=True, region=REG)))
    if CONUS:
        ids = [e["id"] for e in P.events(pid) if e.get("label", "").endswith("(video ranges)")]
        run(pid, "multi_conus", dict(mode="multi", event_ids=ids, params={}))
if A.only in ("", "oyster", "oyster_time") and CONUS:
    # CONUS, but with the recording clock (filename start) trusted to +-900 s; method otherwise unchanged.
    # Gives a margin close to the author's (see PROGRESS Session 4, task 4).
    pid = "oysterbay"; ids = [e["id"] for e in P.events(pid) if e.get("label", "").endswith("(video ranges)")]
    run("oysterbay", "multi_conus_time900", dict(mode="multi", event_ids=ids, params={}, use_start_time=True, clock_uncertainty_s=900))

# ------------------------------------------------------------------ 2. Livermore Zenodo (single-flight, known time)
LV_REG = [37.2, 38.2, -122.5, -121.0]
if A.only in ("", "livermore"):
    truth = json.load(open(f"{EX}/livermore_zenodo_23424278/derived_recorder_position.json"))
    tab = pl.read_csv(f"{EX}/livermore_zenodo_23424278/final_results_40_cases.csv")
    from dopp.adsb import DATA as DAYS_ROOT
    days = set(os.listdir(DAYS_ROOT)) if os.path.isdir(DAYS_ROOT) else set()
    for i, d in enumerate(sorted(glob.glob(f"{EX}/livermore_zenodo_23424278/V_constant_H_constant/*/"))):
        case = os.path.basename(d.rstrip("/")); row = tab.filter(pl.col("case") == case).row(0, named=True)
        day = row["received_start_utc"][:10]
        if day not in days: print(" skip", case, "(no flight data for", day, ")"); continue
        # audio.wav starts at received_start_utc (first frequency sample of frequency_vs_time_used.csv)
        pid = "livermore_" + case.lower()
        ensure(pid, d + "audio.wav", f"Livermore CA {case} ({row['received_start_utc'][:16]}Z, slant {row['R_ADSB_m']/1000:.1f} km)",
               start_utc=ts(row["received_start_utc"]), tz="America/Los_Angeles", day=day,
               extra=dict(preset=True, order=10 + i, truth=[truth["lat"], truth["lon"]],
                          note=f"truth derived from ADS-B tracks (L RMS {truth['rms_L_residual_m']:.1f} m); ADS-B flight {case.split('_')[0]}"))
        base = dict(include_jets=True, single_dt_sweep_s=5.0, single_time_tol_s=5.0, single_coarse_m=0.0)
        if not REGIONAL:
            e = [x for x in P.events(pid) if x.get("source") == "trace"][0]
            if CONUS: run(pid, "single_conus", dict(mode="single", event_ids=[e["id"]], params=base), heat=False)
            continue
        ev = detect(pid, max_events=6, half_window_s=25.0)      # auto candidates kept for comparison (unselected)
        # the dataset's own traced ridge (frequency_vs_time_used.csv) = the manual-trace fallback
        import numpy as np
        csv = pl.read_csv(d + "frequency_vs_time_used.csv")
        t = np.array([ts(x) for x in csv["time_utc"]]) - ts(row["received_start_utc"])
        pts = np.c_[t, csv["frequency_hz"].to_numpy()]; pts = pts[::max(1, len(pts) // 150)]
        e = P.fit_trace(P.spec(pid), pts.tolist()); e["source"] = "trace"; e["label"] = "published ridge (manual-trace fallback)"
        e["id"] = 1 + max([x["id"] for x in ev] + [0]); e["selected"] = True; ev.insert(0, e); P.set_events(pid, ev)
        P.set_meta(pid, default_mode="single", include_jets=True, view=dict(t0=0, t1=float(P.meta(pid)["duration_s"]), f0=50, f1=1300))
        print(" ", case, "trace event t0", round(e["t0"], 2), "f0", round(e["f0"], 1), "v", round(e["v"], 1), "d", round(e["d"]),
              "| auto:", [(round(x["t0"], 1), round(x["f0"]), round(x["conf"], 1)) for x in ev[1:4]], flush=True)
        P.set_meta(pid, region=LV_REG, default_tag="single_region")
        # clock known to ~1 s (recorder timestamps) -> alignment sweep +-5 s, time tolerance 5 s; no coarse pass
        run(pid, "single_region", dict(mode="single", event_ids=[e["id"]], params=dict(base, region=LV_REG)))
        if CONUS: run(pid, "single_conus", dict(mode="single", event_ids=[e["id"]], params=base), heat=False)

# ------------------------------------------------------------------ 3. Altenthann, Bavaria (freesound field recording)
if A.only in ("", "altenthann") and REGIONAL:
    pid = "altenthann"; REG = [48.3, 49.9, 11.0, 13.6]
    # project already created from the freesound HQ preview (847111_preview_hq.mp3); start = mid of the stated window
    P.set_meta(pid, region=REG, default_mode="single", include_jets=False, default_tag="single_region")
    ev = P.events(pid)
    e = min(ev, key=lambda x: abs(x["t0"] - 219) + (0 if abs(x["f0"] - 103) < 10 else 1e6))
    for x in ev: x["selected"] = x is e
    e["label"] = "helicopter (description: ~03:00)"; P.set_events(pid, ev)
    P.set_meta(pid, view=dict(t0=e["t0"] - 120, t1=e["t0"] + 120, f0=40, f1=600))
    # clock known only to the stated 13:30-14:30 CET window -> +-800 s
    run(pid, "single_region", dict(mode="single", event_ids=[e["id"]], params=dict(region=REG, single_time_tol_s=800.0,
                                                                                    single_dt_sweep_s=800.0, single_coarse_m=0.0)))

# ------------------------------------------------------------------ 4. Cornell FeederWatch live cam (VIDEO; planes not the subject)
if A.only in ("", "feederwatch") and REGIONAL:
    pid = "ithaca_feederwatch"; REG = [41.6, 43.4, -77.7, -75.2]
    FW = f"{EX}/ithaca_feederwatch"
    # project created from the HLS capture (clip_20261109T170554Z.mp4); t=0 = 17:05:54Z from the burned-in camera clock.
    # Flights: adsb.lol live API polls (60 nm around the camera) -> day store 2026-11-09 (scripts/ingest_adsblol_live.py)
    if not os.path.exists(os.path.join(P.pdir(pid), "meta.json")):
        P.create_project(f"{FW}/clip_20261109T170554Z.mp4", "FeederWatch cam, Sapsucker Woods, Ithaca NY (live video, 9 Nov 2026)",
                         pid=pid, start_utc=ts("2026-11-09T17:05:54Z"), tz="America/New_York", day="2026-11-09",
                         extra=dict(preset=True, order=4, truth=[42.4800, -76.4513], region=REG))
    ev = P.events(pid) or detect(pid, max_events=40, half_window_s=40.0)
    # the King Air pass (US Army BE20 95-00088, 7.2 km slant, ADS-B heard-CPA 1927 s): strongest auto events ~1890-1920 s
    e = min([x for x in ev if 1880 <= x["t0"] <= 1925 and 100 <= x["f0"] <= 130 and x.get("fit_rms_hz")],
            key=lambda x: x["fit_rms_hz"])
    for x in ev: x["selected"] = x is e
    e["label"] = "faint turboprop pass (auto)"; P.set_events(pid, ev)
    P.set_meta(pid, region=REG, default_mode="single", include_jets=False, default_tag="single_region_wind",
               view=dict(t0=e["t0"] - 150, t1=e["t0"] + 150, f0=50, f1=500))
    run(pid, "single_region", dict(mode="single", event_ids=[e["id"]], params=dict(region=REG, single_coarse_m=0.0)))
    run(pid, "single_region_wind", dict(mode="single", event_ids=[e["id"]], params=dict(region=REG, single_coarse_m=0.0, wind="era5")))

# ------------------------------------------------------------------ 5. Venice Beach live cam (VIDEO; beach cam, planes not the subject)
if A.only in ("", "venice") and REGIONAL:
    pid = "venice_beach"; REG = [33.6, 34.4, -118.95, -118.0]; VB = f"{EX}/venice_beach"
    # Teleport.camera stream EO_1LWqsCNE from the Venice V Hotel (5 Westminster Ave; OSM 33.98864,-118.47493), grabbed from the
    # YouTube DVR (examples/socal_adsb/dvr_grab.py + dvr_concat.py). Segment EXT-X-PROGRAM-DATE-TIME of the first segment =
    # 18:13:58.98Z; the burned-in camera clock reads 1.7 s earlier (seconds tick checked at t=60 s; agrees at t=10/500 s), so
    # t=0 = 18:13:57.28Z (clock known to ~2 s). Flights: adsb.lol live API polls (60 nm) + per-aircraft trace_full backfill
    # (scripts/adsblol_traces.py) -> day store '2026-11-09_socal' (scripts/ingest_adsblol_live.py).
    info = json.load(open(glob.glob(f"{VB}/clip_*Z.json")[0]))
    ensure(pid, f"{VB}/{info['clip']}", "Venice Beach cam, Venice V Hotel, Los Angeles (live video, 9 Nov 2026)",
           start_utc=info["start_utc"] - 1.7, tz="America/Los_Angeles", day="2026-11-09_socal",
           extra=dict(preset=True, order=5, truth=[33.98864, -118.47493], region=REG,
                      note="truth = hotel address (camera on its roof); GA passes along the coast are heard, not seen"))
    ev = P.events(pid) or detect(pid, max_events=80, half_window_s=40.0)
    def pick(t_lo, t_hi, v_lo, v_hi, label):
        c = [x for x in ev if x.get("source", "auto") == "auto" and t_lo <= x["t0"] <= t_hi and v_lo <= x["v"] <= v_hi]
        e = max(c, key=lambda x: x["conf"]); e["label"] = label; return e
    # auto events whose fitted v/d match a heard GA pass (ADS-B heard-CPA time, slant, ground speed in the label)
    e_sr20 = pick(320, 330, 45, 70, "faint SR20 pass (auto) - ADS-B N882AB 325 s, 0.98 km, 111 kt")
    e_pa46 = pick(870, 880, 45, 70, "faint PA46 pass (auto) - ADS-B N350TV 874 s, 0.83 km")
    e_p28r = pick(2310, 2318, 45, 70, "faint P28R pass (auto) - ADS-B N56114 2312 s, 0.66 km, 115 kt")
    e_c172 = pick(2570, 2580, 35, 50, "faint C172 pass (auto) - ADS-B N171CP 2570 s, 0.88 km, 79 kt")
    for x in ev: x["selected"] = x is e_sr20 or x is e_pa46
    P.set_events(pid, ev)
    P.set_meta(pid, region=REG, default_mode="multi", include_jets=False, default_tag="multi_region",
               view=dict(t0=e_sr20["t0"] - 120, t1=e_pa46["t0"] + 120, f0=40, f1=600))
    # clock known to ~2 s (burned-in clock vs segment time) -> alignment/time tolerance +-5 s, as for Livermore
    clk = dict(region=REG, single_coarse_m=0.0, single_dt_sweep_s=5.0, single_time_tol_s=5.0)
    run(pid, "multi_region", dict(mode="multi", event_ids=[e_sr20["id"], e_pa46["id"]], params=dict(region=REG)))
    run(pid, "multi_region_b", dict(mode="multi", event_ids=[e_p28r["id"], e_c172["id"]], params=dict(region=REG)))
    run(pid, "single_region", dict(mode="single", event_ids=[e_pa46["id"]], params=clk))
    run(pid, "single_region_c172", dict(mode="single", event_ids=[e_c172["id"]], params=clk))
    run(pid, "single_region_sr20", dict(mode="single", event_ids=[e_sr20["id"]], params=clk))
    run(pid, "single_region_p28r", dict(mode="single", event_ids=[e_p28r["id"]], params=clk))
    run(pid, "single_region_clock60", dict(mode="single", event_ids=[e_pa46["id"]], params=dict(region=REG, single_coarse_m=0.0)))

# ------------------------------------------------------------------ Altenthann with the optional wind correction (comparison)
if A.only in ("altenthann_wind",) and REGIONAL:
    pid = "altenthann"; REG = [48.3, 49.9, 11.0, 13.6]; e = [x for x in P.events(pid) if x.get("selected")][0]
    base = dict(region=REG, single_time_tol_s=800.0, single_dt_sweep_s=800.0, single_coarse_m=0.0)
    run(pid, "single_region_wind", dict(mode="single", event_ids=[e["id"]], params=dict(base, wind="era5")))
