"""Doppler flyover geolocation -- local web app (FastAPI). Run:  .venv/bin/python server.py --port 8765"""
import os, io, json, time, uuid, threading, traceback, argparse, math, shutil
from dataclasses import asdict, fields
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Body
from fastapi.responses import Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from dopp import project as P
from dopp.adsb import PROVIDERS, AdsbLolProvider
from dopp.search import Params, search_multi, search_single
from dopp import results as R
from dopp.physics import worked_example

APP = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Doppler flyover geolocation")
PROVIDER = AdsbLolProvider()
_STORES = {}; _STORE_LOCK = threading.Lock()
def store(day):
    with _STORE_LOCK:
        if day not in _STORES: _STORES[day] = PROVIDER.load_day(day)
        return _STORES[day]

# ------------------------------------------------------------------ colour map (magma-like)
_ANCH = np.array([[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122], [229, 80, 100],
                  [251, 135, 97], [254, 194, 135], [252, 253, 191]], float)
LUT = np.stack([np.interp(np.linspace(0, 1, 256), np.linspace(0, 1, len(_ANCH)), _ANCH[:, c]) for c in range(3)], 1).astype(np.uint8)

@app.get("/api/info")
def info():
    return dict(days=PROVIDER.available_days(), providers=list(PROVIDERS), params=asdict(Params()),
                worked_example=worked_example())

@app.get("/api/projects")
def projects(): return P.list_projects()

@app.post("/api/projects")
async def upload(file: UploadFile = File(...), name: str = Form(""), start_utc: str = Form(""), tz: str = Form("UTC"), day: str = Form("")):
    tmp = os.path.join(P.PROJ, "_upload_" + uuid.uuid4().hex[:8] + "_" + os.path.basename(file.filename or "media"))
    with open(tmp, "wb") as f: shutil.copyfileobj(file.file, f)
    try:
        m = P.create_project(tmp, name or file.filename, start_utc=_parse_time(start_utc), tz=tz, day=day or None)
    except Exception as e:
        raise HTTPException(400, f"could not decode media: {e}")
    finally:
        os.remove(tmp)
    return m

def _parse_time(s):
    if s in (None, ""): return None
    if isinstance(s, (int, float)): return float(s)
    from datetime import datetime, timezone
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()

@app.get("/api/projects/{pid}")
def get_project(pid: str):
    m = P.meta(pid)
    if not m: raise HTTPException(404)
    sp = P.spec(pid)
    ev = P.events(pid)
    return dict(meta=m, events=ev, gaps=P.gap_table([e for e in ev if e.get("selected")]),
                spec=dict(t0=float(sp.t[0]), t1=float(sp.t[-1]), f0=float(sp.f[0]), f1=float(sp.f[-1]), dt=sp.dt, df=sp.df))

@app.post("/api/projects/{pid}/meta")
def update_meta(pid: str, body: dict = Body(...)):
    kw = {}
    if "start_utc" in body: kw["start_utc"] = _parse_time(body["start_utc"])
    for k in ("tz", "day", "name"):
        if k in body: kw[k] = body[k]
    return P.set_meta(pid, **kw)

@app.get("/api/projects/{pid}/audio.mp3")
def audio(pid: str): return FileResponse(os.path.join(P.pdir(pid), "audio.mp3"), media_type="audio/mpeg")

@app.get("/api/projects/{pid}/spec.png")
def spec_png(pid: str, t0: float, t1: float, f0: float, f1: float, w: int = 1200, h: int = 500, kind: str = "z",
             lo: float = None, hi: float = None):
    sp = P.spec(pid)
    i0 = max(0, int(np.searchsorted(sp.t, t0)) - 1); i1 = min(sp.t.size, int(np.searchsorted(sp.t, t1)) + 1)
    j0 = max(0, int(np.searchsorted(sp.f, f0)) - 1); j1 = min(sp.f.size, int(np.searchsorted(sp.f, f1)) + 1)
    if i1 - i0 < 2 or j1 - j0 < 2: raise HTTPException(400, "empty range")
    A = (sp.z if kind == "z" else sp.db)[i0:i1, j0:j1].astype(np.float32)
    if kind == "z": lo_, hi_ = (-1.0 if lo is None else lo), (6.0 if hi is None else hi)
    else:
        lo_ = np.percentile(A, 5) if lo is None else lo; hi_ = np.percentile(A, 99.7) if hi is None else hi
    # decimate in time if far more columns than pixels (max-pool keeps faint ridges visible)
    k = max(1, (i1 - i0) // (2 * w))
    if k > 1:
        n = (A.shape[0] // k) * k; A = A[:n].reshape(-1, k, A.shape[1]).max(1)
    U = np.clip((A - lo_) / (hi_ - lo_ + 1e-9), 0, 1)
    img = Image.fromarray(LUT[(U.T[::-1] * 255).astype(np.uint8)])
    # map exact requested extent: compute crop in source pixels
    st0, st1 = sp.t[i0], sp.t[i0] + (A.shape[0]) * sp.dt * k
    sf0, sf1 = sp.f[j0], sp.f[j1 - 1] + sp.df
    W, H = img.size
    box = ((t0 - st0) / (st1 - st0) * W, (sf1 - f1) / (sf1 - sf0) * H, (t1 - st0) / (st1 - st0) * W, (sf1 - f0) / (sf1 - sf0) * H)
    img = img.transform((w, h), Image.EXTENT, box, resample=Image.BILINEAR)
    b = io.BytesIO(); img.save(b, "PNG")
    return Response(b.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})

# ------------------------------------------------------------------ events
@app.post("/api/projects/{pid}/detect")
def detect(pid: str, body: dict = Body(default={})):
    sp = P.spec(pid)
    kw = {k: body[k] for k in ("fmin", "fmax", "min_conf", "max_events", "harmonics", "harmonic_fit") if k in body}
    found = P.auto_detect(sp, **kw)
    ev = [e for e in P.events(pid) if e.get("source") != "auto" or body.get("keep_auto")]
    for e in found:
        e["id"] = P._next_id(ev); e["selected"] = False; ev.append(e)
    P.set_events(pid, ev)
    return dict(events=ev, n_found=len(found))

@app.post("/api/projects/{pid}/events")
def add_event(pid: str, body: dict = Body(...)):
    """body: {params:{f0,v,d,t0}} (sliders) or {trace:[[t,f],...], init?:[f0,v,d,t0]}; optional replace_id."""
    sp = P.spec(pid); ev = P.events(pid)
    if body.get("trace"):
        e = P.fit_trace(sp, body["trace"], body.get("init"))
    else:
        q = body["params"]; e = P.event_from_params(sp, float(q["f0"]), float(q["v"]), float(q["d"]), float(q["t0"]),
                                                    float(q.get("t0_sigma", 1.0)))
    for k in ("v_min", "v_max", "d_min", "d_max", "t0_sigma"):
        if k in body.get("overrides", {}): e[k] = float(body["overrides"][k])
    e["v_best"] = 0.5 * (e["v_min"] + e["v_max"]); e["d_best"] = 0.5 * (e["d_min"] + e["d_max"])
    rid = body.get("replace_id")
    if rid is not None:
        old = [x for x in ev if x["id"] == rid]
        e["id"] = rid; e["selected"] = old[0].get("selected", True) if old else True
        ev = [x if x["id"] != rid else e for x in ev]
    else:
        e["id"] = P._next_id(ev); e["selected"] = True; ev.append(e)
    P.set_events(pid, ev)
    return dict(event=e, events=ev, gaps=P.gap_table([x for x in ev if x.get("selected")]))

@app.post("/api/projects/{pid}/events/{eid}")
def patch_event(pid: str, eid: int, body: dict = Body(...)):
    ev = P.events(pid)
    if body.get("delete"): ev = [e for e in ev if e["id"] != eid]
    elif body.get("harmonic_refit"):     # optional joint fit over the event's harmonic comb (dopp/harmonics.py)
        from dopp.harmonics import harmonic_refit
        sp = P.spec(pid)
        ev = [harmonic_refit(sp, e) if e["id"] == eid else e for e in ev]
    else:
        for e in ev:
            if e["id"] == eid:
                for k, v in body.items():
                    if k in ("selected", "v_min", "v_max", "d_min", "d_max", "t0_sigma", "label"): e[k] = v
                e["v_best"] = 0.5 * (e["v_min"] + e["v_max"]); e["d_best"] = 0.5 * (e["d_min"] + e["d_max"])
    P.set_events(pid, ev)
    return dict(events=ev, gaps=P.gap_table([e for e in ev if e.get("selected")]))

# ------------------------------------------------------------------ jobs
JOBS = {}; RUN_LOCK = threading.Lock()

def _params(d):
    names = {f.name for f in fields(Params)}
    q = {k: (tuple(v) if isinstance(v, list) else v) for k, v in (d or {}).items() if k in names}
    return Params(**q)

@app.post("/api/projects/{pid}/search")
def start_search(pid: str, body: dict = Body(...)):
    m = P.meta(pid); ev_all = {e["id"]: e for e in P.events(pid)}
    ids = body.get("event_ids") or [e["id"] for e in ev_all.values() if e.get("selected")]
    ev = sorted([ev_all[i] for i in ids], key=lambda e: e["t0"])
    mode = body.get("mode", "multi" if len(ev) >= 2 else "single")
    day = body.get("day") or m.get("day")
    if not day: raise HTTPException(400, "choose a flight-data day")
    p = _params(body.get("params"))
    if mode == "multi" and len(ev) < 2: raise HTTPException(400, "multi-event search needs >= 2 events")
    if mode == "single":
        if len(ev) != 1: raise HTTPException(400, "single-flight mode needs exactly 1 event")
        if m.get("start_utc") is None: raise HTTPException(400, "single-flight mode needs the recording start time")
    if mode == "multi" and body.get("use_start_time") and m.get("start_utc") is not None:
        w = float(body.get("clock_uncertainty_s", 300))
        tref = m["start_utc"] + ev[0]["t0"]; p.time_window = (tref - w, tref + w)
    jid = uuid.uuid4().hex[:10]
    job = dict(id=jid, pid=pid, mode=mode, day=day, status="queued", progress=0.0, stage="queued", log=[],
               events=ev, params=asdict(p), created=time.time(), rec_start=m.get("start_utc"))
    JOBS[jid] = job
    threading.Thread(target=_run, args=(job, p), daemon=True).start()
    return dict(job=jid)

def _run(job, p):
    def log(s): job["log"].append(f"{time.strftime('%H:%M:%S')} {s}")
    def prog(x, s): job["progress"] = x; job["stage"] = s
    with RUN_LOCK:
        job["status"] = "running"
        try:
            st = store(job["day"]); sp = P.spec(job["pid"]); m = P.meta(job["pid"])
            truth = tuple(m["truth"][:2]) if m.get("truth") else None
            if job["mode"] == "multi":
                res = search_multi(st, sp, job["events"], p, log=log, progress=prog)
                res["mode"] = "multi"; N = len(job["events"])
            else:
                e = job["events"][0]
                res = search_single(st, sp, e, e["line"], job["rec_start"], p, log=log, progress=prog); N = 1
            job["summary"] = R.summarize(res, st, N, truth)
            job["status"] = "done" if res.get("status") == "ok" else "empty"; job["progress"] = 1.0; job["stage"] = res.get("status")
            d = os.path.join(P.pdir(job["pid"]), "results"); os.makedirs(d, exist_ok=True)
            P.save_json(os.path.join(d, job["id"] + ".json"), {k: v for k, v in job.items()})
        except Exception as e:
            job["status"] = "error"; job["error"] = repr(e); log(traceback.format_exc())

def _job(jid):
    if jid in JOBS: return JOBS[jid]
    for pid in os.listdir(P.PROJ):
        f = os.path.join(P.PROJ, pid, "results", jid + ".json")
        if os.path.exists(f): JOBS[jid] = P.load_json(f); return JOBS[jid]
    raise HTTPException(404, "job not found")

@app.get("/api/jobs/{jid}")
def job_status(jid: str, full: int = 0):
    j = _job(jid)
    out = {k: v for k, v in j.items() if k != "summary" and k != "events"} if not full else dict(j)
    out["has_summary"] = "summary" in j
    return out

@app.get("/api/projects/{pid}/results")
def list_results(pid: str):
    d = os.path.join(P.pdir(pid), "results"); out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json") or "_" in f[:-5]: continue      # skip cached curves/heat maps
            j = P.load_json(os.path.join(d, f))
            if j: out.append(dict(id=j["id"], mode=j["mode"], day=j["day"], status=j["status"], created=j["created"],
                                  n_events=len(j["events"]), truth_rank=(j.get("summary") or {}).get("truth_rank"),
                                  preset=j.get("preset", False), tag=j.get("tag")))
    out.sort(key=lambda r: -r["created"])
    return out

def _row(j, rank):
    rows = (j.get("summary") or {}).get("combos") or []
    if not (1 <= rank <= len(rows)): raise HTTPException(404, "rank")
    return rows[rank - 1]

@app.get("/api/jobs/{jid}/curves")
def job_curves(jid: str, rank: int = 1):
    j = _job(jid); row = _row(j, rank)
    cache = os.path.join(P.pdir(j["pid"]), "results", f"{jid}_curves_{rank}.json")
    if os.path.exists(cache): return P.load_json(cache)
    p = _params(j["params"]); st = store(j["day"])
    if j["mode"] == "multi": c, dt = R.curves_multi(st, P.spec(j["pid"]), j["events"], row, p)
    else: c, dt = R.curves_single(st, j["events"][0], row, j["rec_start"], p)
    out = dict(rank=rank, curves=c, dt_s=dt, row=row)
    if j.get("status") == "done": P.save_json(cache, out)
    return out

@app.get("/api/jobs/{jid}/heatmap")
def job_heat(jid: str, rank: int = 1, half_km: float = 1.5, step_m: float = 25.0, cached_only: int = 0):
    j = _job(jid); row = _row(j, rank)
    key = f"heat_{rank}_{half_km}_{step_m}"
    cache = os.path.join(P.pdir(j["pid"]), "results", f"{jid}_{key}.json")
    if os.path.exists(cache): return P.load_json(cache)
    if cached_only: raise HTTPException(404, "not cached")
    p = _params(j["params"]); st = store(j["day"])
    with RUN_LOCK:
        if j["mode"] == "multi": h = R.heat_multi(st, P.spec(j["pid"]), j["events"], row, p, half_km, step_m)
        else: h = R.heat_single(st, j["events"][0], row, j["rec_start"], p, half_km, step_m)
    P.save_json(cache, h)
    return h

app.mount("/", StaticFiles(directory=os.path.join(APP, "web"), html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8765); ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")
