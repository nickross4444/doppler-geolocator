"""Projects (one uploaded recording each), event fitting helpers, JSON conversion of search results."""
import os, json, uuid, shutil, time, math
import numpy as np
from .audio import (extract_audio, extract_playback, compute_spectrogram, Spectrogram, detect_events, refine_event,
                    trace_ridge, V_REL, D_LO, D_HI)
from .physics import retarded_line_curve, fit_readouts

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = os.path.join(APP, "data", "projects")
os.makedirs(PROJ, exist_ok=True)

def pdir(pid): return os.path.join(PROJ, pid)

def _jsonable(o):
    if isinstance(o, dict): return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_jsonable(v) for v in o]
    if isinstance(o, np.ndarray): return _jsonable(o.tolist())
    if isinstance(o, (np.floating,)): o = float(o)
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, float) and not math.isfinite(o): return None
    return o

def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(_jsonable(obj), f)
    os.replace(tmp, path)

def load_json(path, default=None):
    if not os.path.exists(path): return default
    with open(path) as f: return json.load(f)

def create_project(src_path, name, start_utc=None, tz="UTC", day=None, pid=None, extra=None, trim=None):
    """Extract 8 kHz mono audio (+ mp3 for playback) with ffmpeg and compute the whitened spectrogram.
    trim=(start_s, dur_s) optionally cuts the source (used by presets)."""
    pid = pid or uuid.uuid4().hex[:10]
    d = pdir(pid); os.makedirs(d, exist_ok=True)
    src = src_path
    if trim:
        import subprocess
        src = os.path.join(d, "trim.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(trim[0]), "-t", str(trim[1]), "-i", src_path,
                        "-vn", "-ac", "1", "-ar", "16000", src], check=True)
    wav = extract_audio(src, os.path.join(d, "audio.wav"))
    extract_playback(src, os.path.join(d, "audio.mp3"))
    if trim: os.remove(src)
    sp = compute_spectrogram(wav)
    sp.save(os.path.join(d, "spec.npz"))
    meta = dict(id=pid, name=name, source=os.path.basename(src_path), created=time.time(), start_utc=start_utc,
                tz=tz, day=day, duration_s=float(sp.t[-1] + sp.dt), fmax=float(sp.f[-1]), **(extra or {}))
    save_json(os.path.join(d, "meta.json"), meta)
    save_json(os.path.join(d, "events.json"), [])
    return meta

def list_projects():
    out = []
    for p in sorted(os.listdir(PROJ)):
        m = load_json(os.path.join(PROJ, p, "meta.json"))
        if m: out.append(m)
    out.sort(key=lambda m: (not m.get("preset"), m.get("order", 99), -m.get("created", 0)))
    return out

_SPEC = {}
def spec(pid):
    path = os.path.join(pdir(pid), "spec.npz"); mt = os.path.getmtime(path)
    if pid not in _SPEC or _SPEC[pid][0] != mt: _SPEC[pid] = (mt, Spectrogram.load(path))
    return _SPEC[pid][1]

def meta(pid): return load_json(os.path.join(pdir(pid), "meta.json"))
def set_meta(pid, **kw):
    m = meta(pid); m.update(kw); save_json(os.path.join(pdir(pid), "meta.json"), m); return m
def events(pid): return load_json(os.path.join(pdir(pid), "events.json"), [])
def set_events(pid, ev): save_json(os.path.join(pdir(pid), "events.json"), ev)

# ---------------------------------------------------------------- event construction
def _next_id(ev): return 1 + max([e.get("id", 0) for e in ev] + [0])

def event_from_params(sp, f0, v, d, t0, t0_sigma=1.0, source="manual", line=None, rms=None):
    """Event dict from the four slider values. Ranges follow the same 'generous' rule as auto-fit
    (v +-V_REL, d x [D_LO, D_HI]); BEST = midpoint (SPEC §4.1)."""
    if line is None: line = trace_ridge(sp, f0, v, d, t0)
    e = dict(f0=f0, v=v, d=d, t0=t0, t0_sigma=t0_sigma, source=source, model="retarded", line=line,
             v_min=v * (1 - V_REL), v_max=v * (1 + V_REL), d_min=d * D_LO, d_max=d * D_HI)
    e["v_best"] = 0.5 * (e["v_min"] + e["v_max"]); e["d_best"] = 0.5 * (e["d_min"] + e["d_max"])
    if rms is not None: e["fit_rms_hz"] = rms
    e.update(fit_readouts(f0, v, d))
    return e

def fit_trace(sp, pts, init=None):
    """Least-squares fit of the retarded constant-velocity model to user-traced (t, f) points.
    Returns an event with t0_sigma from the covariance (used in the gap tolerance)."""
    from scipy.optimize import least_squares
    L = np.asarray(pts, float); L = L[np.argsort(L[:, 0])]
    given = init is not None
    if init is None:
        t0 = L[np.argmax(-np.gradient(L[:, 1], L[:, 0]) if len(L) > 2 else 0), 0]
        fpre, fpost = L[:, 1].max(), L[:, 1].min()
        f0 = 2 * fpre * fpost / (fpre + fpost); v = 343 * (fpre - fpost) / (fpre + fpost)
        init = [f0, max(20, v), 1500.0, t0]
    fun = lambda q: retarded_line_curve(L[:, 0], q[0], q[1], q[2], q[3]) - L[:, 1]
    best = None
    for dd in ((init[2],) if given else ()) + (300, 800, 1500, 3000, 6000):
        x0 = [init[0], init[1], dd, init[3]]
        try:
            r = least_squares(fun, x0, bounds=([5, 5, 30, L[0, 0] - 60], [5000, 320, 40000, L[-1, 0] + 60]), loss="soft_l1", f_scale=1.0)
        except Exception: continue
        if best is None or r.cost < best.cost: best = r
    f0, v, d, t0 = best.x
    J = best.jac; cov = np.linalg.pinv(J.T @ J) * max(1e-6, np.mean(best.fun ** 2))
    sd = np.sqrt(np.clip(np.diag(cov), 0, None))
    rms = float(np.sqrt(np.mean(best.fun ** 2)))
    return event_from_params(sp, float(f0), float(v), float(d), float(t0), t0_sigma=float(max(0.25, sd[3])),
                             source="trace", line=L.tolist(), rms=rms)

def auto_detect(sp, **kw):
    """Auto-detection for faint curves (whitening + harmonic stacking + template bank + Viterbi ridge trace).
    Each candidate carries a confidence score."""
    ev = detect_events(sp, **kw)
    return [e for e in ev if len(e.get("line", [])) >= 12]

def gap_table(evs):
    """Pairwise gaps between the selected events' fitted t0s (+ tolerances) -- never a constant."""
    from .search import Params
    p = Params()
    ev = sorted(evs, key=lambda e: e["t0"])
    rows = []
    for j in range(len(ev)):
        for k in range(j + 1, len(ev)):
            tol = p.offset_tol_s + p.tol_sigma_k * math.hypot(ev[j].get("t0_sigma", 1.0), ev[k].get("t0_sigma", 1.0))
            rows.append(dict(a=ev[j]["id"], b=ev[k]["id"], gap_s=ev[k]["t0"] - ev[j]["t0"], tol_s=tol))
    return rows
