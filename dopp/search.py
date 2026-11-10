"""Location search (SPEC §4–§7): candidate grid -> CPA/delayed CPA -> N-event offset filter
(author's batch nearest-neighbour) -> two-pass silence filter -> crude ranks -> 100 m densify
-> locked spectrogram scoring with dt/f0 sweeps -> best point per flight combo.

Gap definition (owner clarification): the observed gaps are the differences of the fitted closest-pass
times in the recording, gap_k = t0_k - t0_ref (ref = earliest event), compared with each grid point's
delayed CPA times (cpa_time + slant/c). Every pair (j,k) is checked: |(tau_k - tau_j) - (t0_k - t0_j)| <= tol_jk,
tol_jk = offset_tol_s + tol_sigma_k * sqrt(sig_j^2 + sig_k^2) (sig = each fit's t0 uncertainty).
Nothing is hardcoded to 920 s.
"""
import math, time, json
from dataclasses import dataclass, field, asdict
import numpy as np, polars as pl
import numba as nb
from scipy.spatial import cKDTree
from . import C_SOUND, KT
from .geo import haversine_m, enu, R_EARTH
from .dem import elevation
from .physics import cpa as cpa_one, model_curve, emission_time, track_enu, model_g_many
from .audio import sweep_dt_f0, sweep_f0, curve_score, line_distance_sweep

@dataclass
class Params:
    grid_m: float = 200.0                 # SPEC §4.2
    dense_m: float = 100.0                # SPEC §5.4
    max_radius_m: float = 10000.0         # SPEC §4.2 (10 km)
    include_jets: bool = False            # SPEC §2: drop known jets (unknown types kept)
    offset_tol_s: float = 5.0             # SPEC §4.3 / Q3: '+-5 s variance' wiggle room
    tol_sigma_k: float = 2.0              # + k * sqrt(sig1^2+sig2^2) from the fits' t0 uncertainty
    silence_ratio: float = 1.0            # SILENCE_EXCLUDE_RATIO (slide: max(CPA1,CPA2), no multiplier)
    silence_count_jets: bool | None = None # None: jets count as competing traffic iff include_jets (author: jets off)
    silence_windows: str = "consecutive"  # N>2 generalisation: "consecutive" = each pair of time-adjacent events with its own
                                          # max(CPA_j, CPA_k); "span" = one window first..last with max over all. Same for N=2.
    audibility_radius_m: float = 10000.0  # AUDIBILITY_SEARCH_RADIUS_M (Q5, = grid radius)
    tree_max_lat_deg: float = 50.0        # TREE_QUERY_MAX_LAT_DEG (Q5, northern CONUS edge)
    ranking: str = "author"               # 'author' (as written) | 'corrected' (quieter ranks first)
    half_window_s: float = 45.0           # modelled-curve span around each event's CPA
    curve_dt_s: float = 0.5               # curve sample spacing for scoring
    dt_sweep_s: float = 5.0               # event-1 dt sweep +- (Q8: 'a few seconds')
    dt_step_s: float = 0.25
    f0_sweep_rel: float = 0.06            # f0 sweep +-6 % (Q8; covers f0 bias from speed changes)
    f0_step_rel: float = 0.005
    refine: bool = True                   # second, finer sweep around the best (0.05 s, 0.1 %)
    harmonics: int = 1                    # score the fitted tone only (author scored the measured curve)
    max_fine_points: int = 80000
    region: tuple = None                  # (lat0, lat1, lon0, lon1) or None = whole day store (CONUS)
    time_window: tuple = None             # (t_abs0, t_abs1) bounds for the reference event's delayed CPA
    sample_step_s: float = 5.0            # track sampling for the pair prefilter
    single_time_tol_s: float = 60.0       # single-flight mode: |tau - known time| allowed
    single_dt_sweep_s: float = 60.0       # = recording-clock uncertainty (author knew the time to ~1 s: dt +0.45)
    single_use_distance: bool = False     # video: single-flight mode used velocity bounds + time only
    single_coarse_m: float = 1000.0       # speed-up: coarse pass grid (0 = off), then refine on grid_m
    single_v_widen: float = 0.3           # single-flight: widen v range to v*(1 +- this)
    single_align_sweep_s: float = 4.0     # centred alignment sweep (0 = old absolute dt sweep over the clock window)
    single_keep: int = 400                # coarse cells kept for refinement
    # optional wind correction (NOT in the video: OFF keeps the method exact). Doppler is set by motion relative to the
    # air while ADS-B reports ground velocity. wind: 'off' | 'era5' (Open-Meteo historical winds at each flight's
    # altitude/time) | 'manual' (wind_uv) | 'fit' (grid-search one wind vector per candidate on the top results).
    wind: str = "off"
    wind_model: str = "full"              # 'full' (moving medium, physically complete) | 'air_los' (air-relative source velocity only)
    wind_uv: tuple = None                 # (u_east, v_north) m/s for wind='manual'
    wind_fit_max_ms: float = 20.0         # wind='fit': search |u|,|v| <= this
    wind_fit_step_ms: float = 2.0
    wind_fit_topk: int = 300              # wind='fit': re-score this many best (point, combo) rows
    # spectrogram score normalisation (SPEC Q2/Q7 open: the video never shows it). 'z' = robust z-score per frequency bin
    # (default), 'db_med' = dB above the per-bin 120 s median (scores in dB, like the author's ~10 per event).
    score_norm: str = "z"
    score_agg: str = "mean"               # 'mean' over curve samples | 'sum' (undefined samples count 0)

_WIND_CACHE = {}
def wind_for(store, fid, p, t_abs):
    """Wind vector (u_east, v_north) for one pass, or None (off)."""
    if p.wind in ("off", None, "fit"): return None
    if p.wind == "manual": return tuple(p.wind_uv) if p.wind_uv else None
    key = (store.dir, int(fid), int(t_abs // 600))
    if key not in _WIND_CACHE:
        from .wind import flight_wind
        try: _WIND_CACHE[key] = flight_wind(store, fid, t_abs)
        except Exception as ex: print("wind lookup failed:", ex); _WIND_CACHE[key] = None
    return _WIND_CACHE[key]

def _g(store, fid, p, lat, lon, elev, T):
    """model_g_many with the search's wind setting applied (wind evaluated at the middle of the requested times)."""
    w = wind_for(store, fid, p, float(np.nanmean(T)))
    return model_g_many(store.track(int(fid)), lat, lon, elev, T, wind=w, wind_model=p.wind_model)

def _classes(p):
    return ("prop", "rotor", "unknown", "jet") if p.include_jets else ("prop", "rotor", "unknown")

def _ecef(lat, lon):
    la = np.radians(lat); lo = np.radians(lon)
    return np.c_[np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)] * (R_EARTH / 1000.0)

# ------------------------------------------------------------------ grid helpers (global lattice)
def _row_of(lat, step): return np.round(np.asarray(lat) * 111320.0 / step).astype(np.int64)
def _dlon(row, step): return step / (111320.0 * np.cos(np.radians(row * step / 111320.0)))
def _col_of(lon, row, step): return np.round(np.asarray(lon) / _dlon(row, step)).astype(np.int64)
def pid_encode(row, col): return (row.astype(np.int64) << 32) + (col.astype(np.int64) + (1 << 31))
def pid_decode(pid, step):
    row = pid >> 32; col = (pid & 0xFFFFFFFF) - (1 << 31)
    return row * step / 111320.0, col * _dlon(row, step)

def grid_in_box(lat0, lat1, lon0, lon1, step):
    rows = np.arange(_row_of(lat0, step), _row_of(lat1, step) + 1)
    R, C = [], []
    for r in rows:
        dl = _dlon(r, step); c = np.arange(int(np.floor(lon0 / dl)), int(np.ceil(lon1 / dl)) + 1)
        R.append(np.full(c.size, r)); C.append(c)
    return pid_encode(np.concatenate(R), np.concatenate(C))

# ------------------------------------------------------------------ CPA for many points vs one track
def cpa_many(tr, lats, lons, elevs, c=C_SOUND, half=150):
    """Vectorised CPA of a 1 s track to many observers. Returns cpa_t, slant_d, tau(delayed), speed, idx."""
    n = tr["t"].size
    ok = tr["valid"] & np.isfinite(tr["lat"]) & np.isfinite(tr["alt_m"])
    X = _ecef(tr["lat"], tr["lon"]) * 1000.0
    P = _ecef(lats, lons) * 1000.0
    tree = cKDTree(X[ok]); okidx = np.flatnonzero(ok)
    _, j = tree.query(P); i0 = okidx[j]
    w = np.arange(-half, half + 1)
    I = np.clip(i0[:, None] + w[None, :], 0, n - 1)
    # local exact slant distance: horizontal haversine + vertical
    hz = haversine_m(lats[:, None], lons[:, None], tr["lat"][I], tr["lon"][I])
    vz = tr["alt_m"][I] - elevs[:, None]
    d = np.sqrt(hz * hz + vz * vz)
    d[~ok[I]] = np.inf
    k = np.argmin(d, axis=1); rows = np.arange(lats.size)
    ib = I[rows, k]; db = d[rows, k]
    # parabolic refinement
    km = np.clip(k, 1, 2 * half - 1)
    a = d[rows, km - 1]; b = d[rows, km]; cc = d[rows, km + 1]
    den = a - 2 * b + cc
    off = np.where((den > 0) & np.isfinite(den) & (k > 0) & (k < 2 * half), 0.5 * (a - cc) / np.where(den > 0, den, 1), 0.0)
    off = np.clip(off, -1, 1)
    tc = tr["t"][I[rows, km]] + off
    dc = np.where(off != 0, b - 0.25 * (a - cc) * off, db)
    tc = np.where(off != 0, tc, tr["t"][ib])
    return tc, dc, tc + dc / c, tr["gs"][ib], ib

# ------------------------------------------------------------------ step 1: candidate flights per event
def candidate_flights(store, ev, p, t_lo, t_hi):
    P = store.P
    gs = np.asarray(P["gs_kt"])
    vmin = ev["v_min"] / KT - 6; vmax = ev["v_max"] / KT + 6
    t = np.asarray(P["t"])
    m = (gs >= vmin) & (gs <= vmax) & (t >= t_lo) & (t <= t_hi)
    if p.region:
        la0, la1, lo0, lo1 = p.region; pad = p.max_radius_m / 111320.0 * 1.5
        lat = np.asarray(P["lat"]); lon = np.asarray(P["lon"])
        m &= (lat >= la0 - pad) & (lat <= la1 + pad) & (lon >= lo0 - pad / 0.6) & (lon <= lo1 + pad / 0.6)
    fids = np.unique(np.asarray(P["fid"])[m])
    fids = fids[np.isin(store.cls[fids], _classes(p))]
    return fids

def _samples(store, fids, ev, step, t_lo, t_hi):
    """Track samples (every `step` s) whose ground speed is inside the event's range (+-3 m/s)."""
    out = []
    for f in fids:
        tr = store.track(int(f))
        k = np.arange(0, tr["t"].size, int(step))
        m = tr["valid"][k] & (tr["gs"][k] >= ev["v_min"] - 3) & (tr["gs"][k] <= ev["v_max"] + 3) & \
            (tr["t"][k] >= t_lo) & (tr["t"][k] <= t_hi) & np.isfinite(tr["lat"][k])
        k = k[m]
        if k.size:
            out.append(np.c_[np.full(k.size, f), tr["t"][k], tr["lat"][k], tr["lon"][k], tr["trk"][k], tr["gs"][k]])
    return np.concatenate(out) if out else np.zeros((0, 6))

# ------------------------------------------------------------------ step 2: pair prefilter -> candidate grid points
def _strip_points(s1, s2, d1max, d2max, w1, w2, step):
    """Grid points inside the intersection of two 'CPA strips' (perpendicular to each track at the sample,
    half-length dmax, half-width w). Returns pids."""
    lat0, lon0 = s1[2], s1[3]
    x2, y2 = enu(s2[2], s2[3], lat0, lon0)
    def rect(cx, cy, trk, L, W):
        th = math.radians(trk); u = np.array([math.sin(th), math.cos(th)]); n = np.array([u[1], -u[0]])
        c = np.array([cx, cy])
        return np.array([c + a * W * u + b * L * n for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))])
    A = rect(0, 0, s1[4], d1max, w1); B = rect(float(x2), float(y2), s2[4], d2max, w2)
    poly = _clip(A, B)
    if poly is None or len(poly) < 3: return None
    xmin, ymin = poly.min(0); xmax, ymax = poly.max(0)
    la_lo = lat0 + ymin / 111320.0; la_hi = lat0 + ymax / 111320.0
    cl = math.cos(math.radians(lat0))
    lo_lo = lon0 + xmin / (111320.0 * cl); lo_hi = lon0 + xmax / (111320.0 * cl)
    pids = grid_in_box(la_lo, la_hi, lo_lo, lo_hi, step)
    return pids

def _clip(subject, clipper):
    """Sutherland–Hodgman polygon clipping (convex clipper)."""
    out = list(subject)
    cp = list(clipper)
    area = sum(cp[i - 1][0] * cp[i][1] - cp[i][0] * cp[i - 1][1] for i in range(len(cp)))
    if area < 0: cp = cp[::-1]      # make clipper counter-clockwise
    for i in range(len(cp)):
        a, b = cp[i - 1], cp[i]
        inp = out; out = []
        if not inp: return None
        s = inp[-1]
        def inside(q): return (b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0]) >= 0
        def inter(p1, p2):
            x1, y1 = p1; x2, y2 = p2; x3, y3 = a; x4, y4 = b
            den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
            if abs(den) < 1e-12: return p2
            t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
            return np.array([x1 + t * (x2 - x1), y1 + t * (y2 - y1)])
        for e in inp:
            if inside(e):
                if not inside(s): out.append(inter(s, e))
                out.append(e)
            elif inside(s):
                out.append(inter(s, e))
            s = e
    return np.array(out) if out else None

def pair_candidates(S1, Sk, e1, ek, gap, tol, p, log):
    """Sample pairs (s1, sk) that could be heard `gap` apart at some point within both distance bands."""
    c = C_SOUND
    lo = gap + (e1["d_min"] - ek["d_max"]) / c - tol - p.sample_step_s
    hi = gap + (e1["d_max"] - ek["d_min"]) / c + tol + p.sample_step_s
    if Sk.shape[0] == 0 or S1.shape[0] == 0: return {}
    order = np.argsort(Sk[:, 1]); Sk = Sk[order]
    Xk = _ecef(Sk[:, 2], Sk[:, 3])
    R = (e1["d_max"] + ek["d_max"]) / 1000.0 + 0.5
    # bucket queries by time
    bw = 120.0
    tmin = S1[:, 1].min() + lo
    b1 = np.floor((S1[:, 1] + lo - tmin) / bw).astype(int)
    pairs = {}
    w1 = e1["v_max"] * p.sample_step_s / 2 + 400; wk = ek["v_max"] * p.sample_step_s / 2 + 400
    nsp = 0
    for b in np.unique(b1):
        sel = np.flatnonzero(b1 == b)
        ta = tmin + b * bw; tb = ta + bw + (hi - lo)
        j0, j1 = np.searchsorted(Sk[:, 1], [ta, tb])
        if j1 <= j0: continue
        tree = cKDTree(Xk[j0:j1])
        X1 = _ecef(S1[sel, 2], S1[sel, 3])
        res = tree.query_ball_point(X1, R)
        for ii, js in zip(sel, res):
            if not js: continue
            js = np.asarray(js) + j0
            dtv = Sk[js, 1] - S1[ii, 1]
            js = js[(dtv >= lo) & (dtv <= hi) & (Sk[js, 0] != S1[ii, 0])]
            for j in js:
                nsp += 1
                key = (int(S1[ii, 0]), int(Sk[j, 0]))
                pids = _strip_points(S1[ii], Sk[j], e1["d_max"], ek["d_max"], w1, wk, p.grid_m)
                if pids is not None and pids.size:
                    pairs.setdefault(key, []).append(pids)
    log(f"    sample pairs {nsp}, flight pairs {len(pairs)}")
    return {k: np.unique(np.concatenate(v)) for k, v in pairs.items()}

# ------------------------------------------------------------------ step 4: author's batch nearest neighbour
@nb.njit(cache=True)
def _nn_batch(ptr1, times1, ptr2, times2, gap):
    """best_combo_batch_nearest_neighbor (SPEC §4.3), CSR form. For each point r:
    targets = times1 + gap (we name events chronologically: later = earlier + gap); searchsorted in sorted
    times2; compare lo/hi neighbours; keep argmin |diff| over event-1 flights. Returns (i1, i2, diff)."""
    P = ptr1.size - 1
    bi = np.full(P, -1, np.int64); bj = np.full(P, -1, np.int64); bd = np.full(P, np.inf)
    for r in range(P):
        a0, a1 = ptr1[r], ptr1[r + 1]; c0, c1 = ptr2[r], ptr2[r + 1]
        m = c1 - c0
        if a1 <= a0 or m == 0: continue
        t2 = times2[c0:c1]
        idx = np.argsort(t2); t2s = t2[idx]
        for i in range(a0, a1):
            target = times1[i] + gap
            ins = np.searchsorted(t2s, target)
            lo = min(max(ins - 1, 0), m - 1); hi = min(max(ins, 0), m - 1)
            dlo = (t2s[lo] - times1[i]) - gap; dhi = (t2s[hi] - times1[i]) - gap
            if abs(dlo) <= abs(dhi): d = dlo; j = lo
            else: d = dhi; j = hi
            if abs(d) < abs(bd[r]): bd[r] = d; bi[r] = i; bj[r] = c0 + idx[j]
    return bi, bj, bd

# ------------------------------------------------------------------ main multi-event search
def search_multi(store, spec, events, p: Params, log=print, progress=None):
    """events: list of dicts (t0, t0_sigma, v_min/v_max/v_best, d_min/d_max/d_best, f0). Recording time in s."""
    T0 = time.time()
    ev = sorted([dict(e) for e in events], key=lambda e: e["t0"])
    N = len(ev)
    assert N >= 2, "need >= 2 events (use single-flight mode for 1)"
    for e in ev:
        e.setdefault("t0_sigma", 1.0)
        e.setdefault("v_best", 0.5 * (e["v_min"] + e["v_max"])); e.setdefault("d_best", 0.5 * (e["d_min"] + e["d_max"]))
    gaps = [e["t0"] - ev[0]["t0"] for e in ev]
    tols = [[p.offset_tol_s + p.tol_sigma_k * math.hypot(ev[j]["t0_sigma"], ev[k]["t0_sigma"]) for k in range(N)] for j in range(N)]
    log(f"events {N}; gaps from fitted t0: {[round(g, 2) for g in gaps]} s; tol(ref,k) {[round(tols[0][k], 2) for k in range(N)]} s")
    tw = p.time_window or (float(store.t_start.min()), float(store.t_end.max()))
    # ---- step 1 velocity filter
    S, F = [], []
    for k, e in enumerate(ev):
        lo = tw[0] + gaps[k] - 120; hi = tw[1] + gaps[k] + 120
        fids = candidate_flights(store, e, p, lo, hi); F.append(fids)
        S.append(_samples(store, fids, e, p.sample_step_s, lo, hi))
        log(f"  event {k+1}: {fids.size} velocity-matched flights, {S[-1].shape[0]} samples ({time.time()-T0:.0f}s)")
    if progress: progress(0.15, "pair prefilter")
    # ---- step 2: candidate points per (ref, k) pair
    pts_k = []
    flights_for_pid = [dict() for _ in range(N)]
    for k in range(1, N):
        pairs = pair_candidates(S[0], S[k], ev[0], ev[k], gaps[k], tols[0][k], p, log)
        pk = np.unique(np.concatenate(list(pairs.values()))) if pairs else np.zeros(0, np.int64)
        pts_k.append(pk)
        for (f1, fk), pids in pairs.items():
            flights_for_pid[0].setdefault(f1, []).append(pids)
            flights_for_pid[k].setdefault(fk, []).append(pids)
    cand = pts_k[0]
    for pk in pts_k[1:]: cand = np.intersect1d(cand, pk)
    if p.region:
        la, lo_ = pid_decode(cand, p.grid_m); r = p.region
        cand = cand[(la >= r[0]) & (la <= r[1]) & (lo_ >= r[2]) & (lo_ <= r[3])]
    log(f"  candidate grid points after pair prefilter: {cand.size} ({time.time()-T0:.0f}s)")
    if cand.size == 0: return dict(status="no candidates", log=[])
    lat, lon = pid_decode(cand, p.grid_m)
    elev = elevation(lat, lon)
    if progress: progress(0.3, "CPA per point")
    # ---- step 3 per-event lists (velocity + slant-distance filtered)
    lists = []
    for k, e in enumerate(ev):
        recs = []
        for f, plist in flights_for_pid[k].items():
            pids = np.unique(np.concatenate(plist))
            ii = np.searchsorted(cand, pids); ok = (ii < cand.size)
            ii = ii[ok]; ii = ii[cand[ii] == pids[ok]]
            if ii.size == 0: continue
            tr = store.track(int(f))
            tc, dc, tau, v, _ = cpa_many(tr, lat[ii], lon[ii], elev[ii])
            m = (dc >= e["d_min"]) & (dc <= e["d_max"]) & (v >= e["v_min"]) & (v <= e["v_max"])
            if m.any():
                recs.append(np.c_[ii[m], np.full(m.sum(), f), tc[m], dc[m], tau[m], v[m]])
        R = np.concatenate(recs) if recs else np.zeros((0, 6))
        R = R[np.lexsort((R[:, 4], R[:, 0]))]
        lists.append(R)
        log(f"  event {k+1}: {R.shape[0]} (point, flight) CPA records in range")
    # ---- step 4 offset filter (reference = event 1, all pairs checked)
    P = cand.size
    ptr = [np.searchsorted(L[:, 0], np.arange(P + 1)).astype(np.int64) for L in lists]
    best_i = None; sel = {}
    ok = np.ones(P, bool)
    for k in range(1, N):
        bi, bj, bd = _nn_batch(ptr[0], lists[0][:, 4].copy(), ptr[k], lists[k][:, 4].copy(), gaps[k])
        sel[k] = (bi, bj, bd)
        ok &= (bi >= 0) & (np.abs(bd) <= tols[0][k])
    # N>2: the SAME event-1 record must be used for every k. Where the per-pair nearest-neighbour picks disagree,
    # re-pick: try each event-1 record of the point, take the nearest event-k record to tau1 + gap_k for every k,
    # keep the record whose worst normalised offset is smallest (dropped if any pair is out of tolerance).
    if N > 2:
        for r in np.flatnonzero(ok):
            if len({int(sel[k][0][r]) for k in range(1, N)}) == 1: continue
            a0, a1 = ptr[0][r], ptr[0][r + 1]; best = None
            for i1 in range(a0, a1):
                t1 = lists[0][i1, 4]; picks = []; worst = 0.0
                for k in range(1, N):
                    b0, b1 = ptr[k][r], ptr[k][r + 1]
                    tk = lists[k][b0:b1, 4]; j = int(np.abs(tk - (t1 + gaps[k])).argmin()); dd = tk[j] - t1 - gaps[k]
                    worst = max(worst, abs(dd) / tols[0][k]); picks.append((b0 + j, dd))
                if worst <= 1 and (best is None or worst < best[0]): best = (worst, i1, picks)
            if best is None: ok[r] = False; continue
            for k, (j, dd) in zip(range(1, N), best[2]):
                sel[k][0][r] = best[1]; sel[k][1][r] = j; sel[k][2][r] = dd
    idx = np.flatnonzero(ok)
    rows = []
    for r in idx:
        i1 = sel[1][0][r]
        combo = [lists[0][i1]] + [lists[k][sel[k][1][r]] for k in range(1, N)]
        taus = [c[4] for c in combo]
        good = True
        for j in range(N):
            for k in range(j + 1, N):
                if abs((taus[k] - taus[j]) - (gaps[k] - gaps[j])) > tols[j][k]: good = False
        if not good: continue
        if p.time_window and not (p.time_window[0] <= taus[0] <= p.time_window[1]): continue
        rows.append((r, combo))
    log(f"  offset filter survivors: {len(rows)} ({time.time()-T0:.0f}s)")
    if progress: progress(0.45, "silence filter")
    _sj = p.include_jets if p.silence_count_jets is None else p.silence_count_jets
    nonjet = set(np.flatnonzero(store.cls != "jet").tolist()) if not _sj else None
    # own lists for pass 1 (all events' in-range records at the point)
    recs = []
    for (r, combo) in rows:
        d = dict(pid=int(cand[r]), lat=float(lat[r]), lon=float(lon[r]), elev=float(elev[r]))
        for k, c in enumerate(combo):
            d[f"flightid_{k+1}"] = int(c[1]); d[f"cpa_time_utc_{k+1}"] = float(c[4]); d[f"cpa_distance_m_{k+1}"] = float(c[3])
            d[f"seg_speed_mps_{k+1}"] = float(c[5]); d[f"cpa_time_raw_{k+1}"] = float(c[2])
        d["predicted_gap_s"] = float(combo[-1][4] - combo[0][4]); d["observed_gap_s"] = float(gaps[-1])
        d["time_diff_s"] = d["predicted_gap_s"] - gaps[-1]; d["abs_time_diff_s"] = abs(d["time_diff_s"])
        for k in range(N):
            a, b = ptr[k][r], ptr[k][r + 1]
            d[f"all_flightids_{k+1}"] = lists[k][a:b, 1].astype(int).tolist()
            d[f"all_cpa_times_{k+1}"] = lists[k][a:b, 4].tolist(); d[f"all_cpa_distances_{k+1}"] = lists[k][a:b, 3].tolist()
        recs.append(d)
    res = dict(events=ev, gaps=gaps, tolerances=tols, params=asdict(p), n_candidates=int(P), n_offset=len(recs))
    if not recs: res["status"] = "no offset survivors"; return res
    # ---- step 5 silence filter: pass 1 (own lists) + pass 2 (spatial lookup)
    surv = []
    for d in recs:
        exc, closest = cheap_silence_check(d, N, nonjet, p)
        d["silence_penalty"] = 0.0 if closest is None else -closest
        if not exc: surv.append(d)
    log(f"  silence pass 1: {len(recs)} -> {len(surv)}")
    _ts = [d[f"cpa_time_utc_{k+1}"] for d in surv for k in range(N)]
    tree = SilenceIndex(store, p, min(_ts) - 60 if surv else 0, max(_ts) + 60 if surv else 0, surv)
    surv2 = []
    for d in surv:
        exc, closest = False, None
        for (w_lo, w_hi, own) in silence_windows(d, N, p):
            e_, c_ = tree.lookup(d["lat"], d["lon"], w_lo, w_hi, [d[f"flightid_{k+1}"] for k in range(N)], own)
            exc |= e_
            if c_ is not None: closest = c_ if closest is None else min(closest, c_)
        d["silence_penalty"] = 0.0 if closest is None else -closest
        if not exc: surv2.append(d)
    log(f"  silence pass 2: {len(surv)} -> {len(surv2)} ({time.time()-T0:.0f}s)")
    res.update(n_silence1=len(surv), n_silence2=len(surv2))
    if not surv2: res["status"] = "no silence survivors"; return res
    # ---- crude scores + ranks (verbatim)
    for d in surv2:
        d["match_score"] = sum(crude_speed_distance_score(d[f"seg_speed_mps_{k+1}"], d[f"cpa_distance_m_{k+1}"],
                                                          ev[k]["v_best"], ev[k]["d_best"]) for k in range(N))
    df = finalize_ranks(pl.DataFrame([{k: v for k, v in d.items() if not k.startswith("all_")} for d in surv2]), p.ranking)
    if progress: progress(0.55, "densify + fine scoring")
    # ---- densify 200 m -> 100 m (2x2 sub-cells) and fine locked scoring
    dense = densify(df, p, N)
    if len(dense) > p.max_fine_points:
        dense = dense.sort("combined_rank").head(p.max_fine_points)
    log(f"  densified points: {len(dense)}")
    scored = fine_score(store, spec, ev, dense, p, log, progress)
    res["status"] = "ok"
    res.update(n_dense=len(dense), elapsed_s=time.time() - T0)
    res["coarse"] = df
    if p.wind == "fit":
        def rescore(sub, pp):
            la = sub["lat"].to_numpy(); lo = sub["lon"].to_numpy(); el = sub["elev_m"].to_numpy()
            cb = np.stack([sub[f"flightid_{k+1}"].to_numpy() for k in range(N)], 1)
            sc, bdt, bf, _, _, _ = score_points(store, spec, ev, la, lo, el, cb, pp)
            return sub.with_columns([pl.Series(f"event{k+1}_score", sc[:, k]) for k in range(N)] + [pl.Series("dt_s", bdt)] +
                                    [pl.Series(f"f0_{k+1}", bf[:, k]) for k in range(N)] + [pl.Series("total_score", sc.sum(1))])
        scored = wind_fit(scored, N, rescore, p, log)
    res["scored"] = scored
    res["combos"] = best_per_combo(scored, N)
    log(f"  done in {time.time()-T0:.0f}s")
    return res

def wind_fit(scored, N, rescore, p, log=print):
    """wind='fit': one constant wind vector per (point, combo), grid-searched on the best rows (top wind_fit_topk/10 combos,
    10 best points each). The wind=0 vector is in the grid, so a row's score can only improve; rows not re-scored keep
    their no-wind score. Adds wind_u / wind_v columns (m/s, toward east / north)."""
    from dataclasses import replace
    t = time.time()
    keys = [f"flightid_{k+1}" for k in range(N)]
    scored = scored.with_row_index("_rid").with_columns(pl.lit(0.0).alias("wind_u"), pl.lit(0.0).alias("wind_v"))
    top = best_per_combo(scored, N).head(max(1, p.wind_fit_topk // 10)).select(keys)
    sub = scored.join(top, on=keys, how="semi").sort("total_score", descending=True).group_by(keys, maintain_order=True).head(10).sort("_rid")
    g = np.arange(-p.wind_fit_max_ms, p.wind_fit_max_ms + 1e-9, p.wind_fit_step_ms)
    best = sub
    for u in g:
        for v in g:
            if u == 0 and v == 0: continue
            r = rescore(sub, replace(p, wind="manual", wind_uv=(float(u), float(v)))).with_columns(pl.lit(float(u)).alias("wind_u"), pl.lit(float(v)).alias("wind_v"))
            m = r["total_score"].to_numpy() > best["total_score"].to_numpy()
            if m.any():
                best = pl.concat([best.filter(pl.Series(~m)), r.filter(pl.Series(m))]).sort("_rid")
    out = pl.concat([scored.join(best.select("_rid"), on="_rid", how="anti"), best.select(scored.columns)])
    log(f"  wind fit: {len(best)} rows x {g.size ** 2} wind vectors ({time.time()-t:.0f}s)")
    return out.drop("_rid").sort("total_score", descending=True)

def silence_windows(d, N, p):
    """[(t_lo, t_hi, own_max_dist)] per the author's rule (window between the CPAs, threshold max of their distances)."""
    ev = sorted((d[f"cpa_time_utc_{k+1}"], d[f"cpa_distance_m_{k+1}"]) for k in range(N))
    if p.silence_windows == "span" or N == 2:
        return [(ev[0][0], ev[-1][0], max(e[1] for e in ev))]
    return [(a[0], b[0], max(a[1], b[1])) for a, b in zip(ev[:-1], ev[1:])]

def cheap_silence_check(d, N, nonjet, p):
    """cheap_silence_check_from_own_lists (verbatim logic, SPEC §4.4) generalised to N events."""
    mine = {d[f"flightid_{k+1}"] for k in range(N)}
    closest = None; exc = False
    for (w_lo, w_hi, own_max_dist) in silence_windows(d, N, p):
        thr = own_max_dist * p.silence_ratio; cw = None
        for k in range(N):
            for fid, t, dist in zip(d[f"all_flightids_{k+1}"], d[f"all_cpa_times_{k+1}"], d[f"all_cpa_distances_{k+1}"]):
                if fid in mine: continue          # either one IS the signal being tested
                if nonjet is not None and fid not in nonjet: continue   # jets don't count as competing traffic
                if w_lo <= t <= w_hi and (cw is None or dist < cw): cw = dist
        if cw is not None:
            exc |= cw < thr
            closest = cw if closest is None else min(closest, cw)
    return exc, closest

class SilenceIndex:
    """build_track_spatial_index + full_silence_check_spatial_lookup (SPEC §4.4), verbatim logic:
    cKDTree over raw (non-jet) trackpoints in (lat, lon) DEGREE space; conservative degree radius;
    exact haversine check; time mask on raw trackpoint timestamps; exclude the combo's own flights."""
    def __init__(self, store, p, t_lo, t_hi, surv):
        P = store.P; self.p = p
        t = np.asarray(P["t"])
        m = (t >= t_lo) & (t <= t_hi)
        if surv:
            la = np.array([d["lat"] for d in surv]); lo = np.array([d["lon"] for d in surv])
            pad = p.audibility_radius_m / 111320.0 * 2
            lat = np.asarray(P["lat"]); lon = np.asarray(P["lon"])
            m &= (lat >= la.min() - pad) & (lat <= la.max() + pad) & (lon >= lo.min() - pad * 2) & (lon <= lo.max() + pad * 2)
        idx = np.flatnonzero(m)
        fid = np.asarray(P["fid"])[idx]
        if not (p.include_jets if p.silence_count_jets is None else p.silence_count_jets):
            k = store.cls[fid] != "jet"; idx = idx[k]; fid = fid[k]
        self.lat = np.asarray(P["lat"])[idx]; self.lon = np.asarray(P["lon"])[idx]
        self.fid = fid; self.ts = t[idx]
        self.tree = cKDTree(np.column_stack([self.lat, self.lon])) if idx.size else None

    def lookup(self, cand_lat, cand_lon, w_lo, w_hi, fids, own_max_dist):
        if self.tree is None: return False, None
        exclude_threshold = own_max_dist * self.p.silence_ratio
        search_radius_m = min(self.p.audibility_radius_m, exclude_threshold)
        meters_per_deg_lon_min = 111320.0 * math.cos(math.radians(self.p.tree_max_lat_deg))
        degree_radius = search_radius_m / meters_per_deg_lon_min
        nearby = self.tree.query_ball_point([cand_lat, cand_lon], r=degree_radius)
        if not nearby: return False, None
        nearby = np.array(nearby)
        keep = (self.ts[nearby] >= w_lo) & (self.ts[nearby] <= w_hi) & ~np.isin(self.fid[nearby], fids)
        if not keep.any(): return False, None
        q = nearby[keep]
        dists = haversine_m(cand_lat, cand_lon, self.lat[q], self.lon[q])
        closest = float(dists.min())
        return closest < exclude_threshold, closest

def crude_speed_distance_score(seg_speed_mps, cpa_distance_m, v_audio_best, d_best):
    speed_rel_err = abs(seg_speed_mps - v_audio_best) / v_audio_best
    dist_rel_err = abs(cpa_distance_m - d_best) / d_best
    return -(speed_rel_err + dist_rel_err)

def finalize_ranks(df, ranking="author"):
    """Verbatim: silence_rank = ordinal rank of silence_penalty; combined_rank = ordinal rank of
    (match_score rank desc + silence_penalty rank ASC). ranking='corrected' ranks quieter (higher
    silence_penalty) first, which is what the docstring's 'higher = quieter = better' implies."""
    desc_sil = ranking == "corrected"
    df = df.with_columns(pl.col("silence_penalty").rank(method="ordinal").cast(pl.Int64).alias("silence_rank"))
    df = df.with_columns([pl.col("match_score").rank(method="ordinal", descending=True).alias("_match_rank"),
                          pl.col("silence_penalty").rank(method="ordinal", descending=desc_sil).alias("_silence_rank_for_combo")])
    df = df.with_columns((pl.col("_match_rank") + pl.col("_silence_rank_for_combo")).alias("_rank_sum"))
    df = df.with_columns(pl.col("_rank_sum").rank(method="ordinal").cast(pl.Int64).alias("combined_rank"))
    return df.drop(["_match_rank", "_silence_rank_for_combo", "_rank_sum"])

def densify(df, p, N):
    """Each surviving 200 m point -> the four 100 m sub-cell centres (+-50 m E/N)."""
    h = p.dense_m / 2
    rows = []
    cols = [c for c in df.columns]
    for d in df.iter_rows(named=True):
        for dx in (-h, h):
            for dy in (-h, h):
                e = dict(d)
                e["lat"] = d["lat"] + dy / 111320.0
                e["lon"] = d["lon"] + dx / (111320.0 * math.cos(math.radians(d["lat"])))
                e["parent_pid"] = d["pid"]
                rows.append(e)
    out = pl.DataFrame(rows)
    return out

# ------------------------------------------------------------------ fine locked scoring
def _curve_g(tr, lat, lon, elev, T_abs):
    f, te, vr, r = model_curve(tr, lat, lon, elev, T_abs, 1.0)
    return f   # = c/(c+v_r) for f0=1

def _track_window(tr, t_lo, t_hi):
    i0 = max(0, np.searchsorted(tr["t"], t_lo) - 2); i1 = min(tr["t"].size, np.searchsorted(tr["t"], t_hi) + 2)
    return {k: v[i0:i1] for k, v in tr.items()}

@nb.njit(parallel=True, cache=True)
def _batch_locked(Z, zt0, zdt, zf0, zdf, trec, G, f0c, dts, f0rel, harmonics, lock_dt, fixed_dt, agg):
    """For each point q, event e: G[q,e,:] curve ratios at reception samples trec[e,:] (recording time,
    before dt). Event 0: sweep dt x f0 (unless lock_dt -> fixed_dt[q]); events >0: dt locked to event 0's
    best, f0 sweep only. Returns scores[q,e], dt[q], f0[q,e]."""
    Q, E, S = G.shape
    sc = np.full((Q, E), -1e9); bdt = np.zeros(Q); bf = np.zeros((Q, E))
    for q in nb.prange(Q):
        if lock_dt:
            dtb = fixed_dt[q]; s0 = -1e18; f0b = 0.0
            for b in range(f0rel.size):
                s = curve_score(Z, zt0, zdt, zf0, zdf, trec[0] + dtb, G[q, 0], f0c[0] * f0rel[b], harmonics, agg)
                if s > s0: s0 = s; f0b = f0c[0] * f0rel[b]
        else:
            s0 = -1e18; dtb = 0.0; f0b = 0.0
            for a in range(dts.size):
                tt = trec[0] + dts[a]
                for b in range(f0rel.size):
                    s = curve_score(Z, zt0, zdt, zf0, zdf, tt, G[q, 0], f0c[0] * f0rel[b], harmonics, agg)
                    if s > s0: s0 = s; dtb = dts[a]; f0b = f0c[0] * f0rel[b]
        sc[q, 0] = s0; bdt[q] = dtb; bf[q, 0] = f0b
        for e in range(1, E):
            se = -1e18; fe = 0.0
            for b in range(f0rel.size):
                s = curve_score(Z, zt0, zdt, zf0, zdf, trec[e] + dtb, G[q, e], f0c[e] * f0rel[b], harmonics, agg)
                if s > se: se = s; fe = f0c[e] * f0rel[b]
            sc[q, e] = se; bf[q, e] = fe
    return sc, bdt, bf

_ZCACHE = {}
def score_Z(spec, p):
    if p.score_norm == "z": return spec.z
    key = (id(spec), p.score_norm)
    if key not in _ZCACHE:
        from .audio import median_removed
        hop = spec.params.get("hop_s", spec.dt); blk = spec.params.get("block_s", 120.0)
        if p.score_norm == "db_med": Z = median_removed(spec.db.T, hop, blk).T.copy()
        else: raise ValueError(f"score_norm {p.score_norm}")
        if len(_ZCACHE) > 8: _ZCACHE.clear()
        _ZCACHE[key] = np.ascontiguousarray(Z, dtype=np.float32)
    return _ZCACHE[key]

def model_ratios(store, ev, lat, lon, elev, fids, t_ref_abs, p):
    """Curve ratios g = c/(c+v_r) for each event at the recording-time sample grid. Locked mapping:
    abs reception time T = t_ref_abs + (t_rec - t0_ref) (t_ref_abs = point's delayed CPA for event 1)."""
    S = int(round(2 * p.half_window_s / p.curve_dt_s)) + 1
    off = np.linspace(-p.half_window_s, p.half_window_s, S)
    G = np.full((len(ev), S), np.nan); trec = np.zeros((len(ev), S))
    for k, e in enumerate(ev):
        trec[k] = e["t0"] + off
        T = t_ref_abs + (trec[k] - ev[0]["t0"])
        tr = _track_window(store.track(int(fids[k])), T[0] - 60, T[-1] + 10)
        if tr["t"].size < 3: continue
        G[k] = _curve_g(tr, lat, lon, elev, T)
    return trec, G

def _sweeps(p):
    dts = np.arange(-p.dt_sweep_s, p.dt_sweep_s + 1e-9, p.dt_step_s)
    f0rel = 1 + np.arange(-p.f0_sweep_rel, p.f0_sweep_rel + 1e-12, p.f0_step_rel)
    return dts, f0rel

def point_taus(store, fids, lat, lon, elev):
    """Delayed CPA time of each point for its own flight (grouped per flight, vectorised)."""
    tau = np.full(len(lat), np.nan)
    for f in np.unique(fids):
        idx = np.flatnonzero(fids == f)
        _, _, t_, _, _ = cpa_many(store.track(int(f)), lat[idx], lon[idx], elev[idx])
        tau[idx] = t_
    return tau

def score_points(store, spec, ev, lat, lon, elev, combos, p, lock_dt=False, fixed_dt=None):
    """Exact emission-time model for every point & event + locked sweep (SPEC §5.2)."""
    lat = np.asarray(lat, float); lon = np.asarray(lon, float); elev = np.asarray(elev, float)
    combos = np.asarray(combos); Q = len(lat); N = len(ev)
    S = int(round(2 * p.half_window_s / p.curve_dt_s)) + 1
    off = np.linspace(-p.half_window_s, p.half_window_s, S)
    TR = np.stack([e["t0"] + off for e in ev])
    tau1 = point_taus(store, combos[:, 0], lat, lon, elev)
    G = np.full((Q, N, S), np.nan)
    for k in range(N):
        T = tau1[:, None] + (TR[k][None, :] - ev[0]["t0"])
        for f in np.unique(combos[:, k]):
            idx = np.flatnonzero(combos[:, k] == f)
            G[idx, k, :] = _g(store, f, p, lat[idx], lon[idx], elev[idx], T[idx])
    dts, f0rel = _sweeps(p)
    f0c = np.array([e["f0"] for e in ev])
    Z = score_Z(spec, p); agg = 1 if p.score_agg == "sum" else 0
    sc, bdt, bf = _batch_locked(Z, spec.t[0], spec.dt, spec.f[0], spec.df, TR, G, f0c, dts, f0rel, p.harmonics,
                                lock_dt, fixed_dt if fixed_dt is not None else np.zeros(Q), agg)
    if p.refine and not lock_dt:
        fdt = np.arange(-p.dt_step_s, p.dt_step_s + 1e-9, 0.05)
        fr = 1 + np.arange(-p.f0_step_rel, p.f0_step_rel + 1e-12, 0.001)
        sc2, bdt2, bf2 = _batch_refine(Z, spec.t[0], spec.dt, spec.f[0], spec.df, TR, G, bf, bdt, fdt, fr, p.harmonics, agg)
        better = sc2[:, 0] >= sc[:, 0]
        sc[better] = sc2[better]; bdt[better] = bdt2[better]; bf[better] = bf2[better]
    return sc, bdt, bf, G, TR, tau1

@nb.njit(parallel=True, cache=True)
def _batch_refine(Z, zt0, zdt, zf0, zdf, trec, G, bf0, bdt0, fdt, fr, harmonics, agg):
    Q, E, S = G.shape
    sc = np.full((Q, E), -1e9); bdt = np.zeros(Q); bf = np.zeros((Q, E))
    for q in nb.prange(Q):
        s0 = -1e18; dtb = 0.0; f0b = 0.0
        for a in range(fdt.size):
            tt = trec[0] + bdt0[q] + fdt[a]
            for b in range(fr.size):
                s = curve_score(Z, zt0, zdt, zf0, zdf, tt, G[q, 0], bf0[q, 0] * fr[b], harmonics, agg)
                if s > s0: s0 = s; dtb = bdt0[q] + fdt[a]; f0b = bf0[q, 0] * fr[b]
        sc[q, 0] = s0; bdt[q] = dtb; bf[q, 0] = f0b
        for e in range(1, E):
            se = -1e18; fe = 0.0
            for b in range(fr.size):
                s = curve_score(Z, zt0, zdt, zf0, zdf, trec[e] + dtb, G[q, e], bf0[q, e] * fr[b], harmonics, agg)
                if s > se: se = s; fe = bf0[q, e] * fr[b]
            sc[q, e] = se; bf[q, e] = fe
    return sc, bdt, bf

@nb.njit(parallel=True, cache=True)
def _batch_line(t_line, f_line, t_curve, G, dts, f0s, fdt, ffr):
    """Coarse sweep (dts x f0s) then fine sweep around the best (fdt offsets x ffr ratios)."""
    Q = G.shape[0]
    out = np.empty(Q); odt = np.empty(Q); of = np.empty(Q)
    for q in nb.prange(Q):
        s, d_, f_ = line_distance_sweep(t_line, f_line, t_curve, G[q], dts, f0s)
        s2, d2, f2 = line_distance_sweep(t_line, f_line, t_curve, G[q], d_ + fdt, f_ * ffr)
        if s2 >= s: s = s2; d_ = d2; f_ = f2
        out[q] = s; odt[q] = d_; of[q] = f_
    return out, odt, of

@nb.njit(parallel=True, cache=True)
def _batch_line_c(t_line, f_line, t_curve, G, dt0, dts, f0s, fdt, ffr):
    """As _batch_line but the dt sweep is centred per point on dt0[q] (the alignment implied by that
    candidate's own delayed CPA time vs the event's fitted t0)."""
    Q = G.shape[0]
    out = np.empty(Q); odt = np.empty(Q); of = np.empty(Q)
    for q in nb.prange(Q):
        s, d_, f_ = line_distance_sweep(t_line, f_line, t_curve, G[q], dt0[q] + dts, f0s)
        s2, d2, f2 = line_distance_sweep(t_line, f_line, t_curve, G[q], d_ + fdt, f_ * ffr)
        if s2 >= s: s = s2; d_ = d2; f_ = f2
        out[q] = s; odt[q] = d_; of[q] = f_
    return out, odt, of

def fine_score(store, spec, ev, dense, p, log, progress=None):
    N = len(ev)
    lat = dense["lat"].to_numpy(); lon = dense["lon"].to_numpy()
    elev = elevation(lat, lon)
    combos = np.stack([dense[f"flightid_{k+1}"].to_numpy() for k in range(N)], 1)
    t = time.time()
    sc, bdt, bf, _, _, tau1 = score_points(store, spec, ev, lat, lon, elev, combos, p)
    log(f"  fine scoring {len(lat)} points: {time.time()-t:.0f}s")
    out = dense.with_columns([pl.Series(f"event{k+1}_score", sc[:, k]) for k in range(N)] +
                             [pl.Series("dt_s", bdt)] + [pl.Series(f"f0_{k+1}", bf[:, k]) for k in range(N)] +
                             [pl.Series("elev_m", elev), pl.Series("tau1_model", tau1)])
    out = out.with_columns(pl.sum_horizontal([f"event{k+1}_score" for k in range(N)]).alias("total_score"))
    return out.sort("total_score", descending=True)

def best_per_combo(scored, N):
    """'show only best point per combo (by match_score)': group by flight-pair (N-tuple) and keep the max."""
    keys = [f"flightid_{k+1}" for k in range(N)]
    return scored.sort("total_score", descending=True).group_by(keys, maintain_order=True).first().sort("total_score", descending=True)

# ------------------------------------------------------------------ single flight, known time
def search_single(store, spec, event, line, rec_start_abs, p: Params, log=print, progress=None):
    """SPEC §7. event: v/d ranges + t0 (recording s) + f0; line: [[t_rec, f], ...] traced points;
    rec_start_abs: absolute UTC epoch of recording t=0. Only segments at that time; scored by
    -mean |f_model - f_line| after dt/f0 sweep; histogram + best point per flight.
    Speed-up (deviation, documented): a coarse pass on a single_coarse_m grid, then the best
    single_keep coarse cells are re-scored on the normal grid (grid_m) inside each cell."""
    T0 = time.time()
    e = dict(event)
    if not p.single_use_distance:
        e["d_min"], e["d_max"] = 0.0, p.max_radius_m
    if p.single_v_widen:   # truncated / faint passes constrain v poorly; the line score does the discrimination
        e["v_min"] = min(e["v_min"], e["v"] * (1 - p.single_v_widen)); e["v_max"] = max(e["v_max"], e["v"] * (1 + p.single_v_widen))
    T_ev = rec_start_abs + e["t0"]
    tol = p.single_time_tol_s
    fids = candidate_flights(store, e, p, T_ev - tol - 60, T_ev + tol + 30)
    log(f"single-flight: v range {e['v_min']:.0f}-{e['v_max']:.0f} m/s; : known event time {T_ev:.0f}; {fids.size} velocity-matched flights")
    line = np.asarray(line, float)
    tl = line[:, 0]; fl = line[:, 1]
    cstep = max(0.25, p.single_dt_sweep_s / 60.0)
    dts = np.arange(-p.single_dt_sweep_s, p.single_dt_sweep_s + 1e-9, cstep)
    f0rel = 1 + np.arange(-p.f0_sweep_rel, p.f0_sweep_rel + 1e-12, p.f0_step_rel)
    fdt = np.arange(-cstep, cstep + 1e-9, 0.05); ffr = 1 + np.arange(-p.f0_step_rel, p.f0_step_rel + 1e-12, 0.001)
    tcurve = np.arange(tl.min() - p.single_dt_sweep_s - 1, tl.max() + p.single_dt_sweep_s + 1, 0.25)
    Tabs = rec_start_abs + tcurve
    # 'centred' alignment (default): each candidate's curve is placed so its delayed CPA sits at the event's
    # fitted t0, then swept +-single_align_sweep_s; the clock error only enters through the time filter
    centred = p.single_align_sweep_s > 0
    sw = p.single_align_sweep_s
    tcl = np.arange(tl.min() - sw - 1, tl.max() + sw + 1, 0.25)   # model times relative to (tau - t0) shift
    dts_c = np.arange(-sw, sw + 1e-9, 0.25); fdt_c = np.arange(-0.25, 0.2501, 0.05)

    def records(flist, step, boxes=None):
        recs = []
        for f in flist:
            tr = store.track(int(f))
            k = (tr["t"] >= T_ev - tol - e["d_max"] / C_SOUND - 5) & (tr["t"] <= T_ev + tol + 5) & tr["valid"] & \
                (tr["gs"] >= e["v_min"] - 3) & (tr["gs"] <= e["v_max"] + 3)
            if k.sum() < 2: continue
            if boxes is None:
                pids = corridor_pids(tr["lat"][k], tr["lon"][k], e["d_max"], step)
            else:
                pids = np.unique(np.concatenate([grid_in_box(*bx, step) for bx in boxes[int(f)]]))
            if p.region:
                pla, plo = pid_decode(pids, step); r = p.region
                pids = pids[(pla >= r[0]) & (pla <= r[1]) & (plo >= r[2]) & (plo <= r[3])]
            if pids.size == 0: continue
            plat, plon = pid_decode(pids, step); pel = elevation(plat, plon)
            # CPA of THIS pass: search only the part of the track around the known time (a circling or
            # pattern-flying aircraft has other, closer passes elsewhere in its track)
            trw = _track_window(tr, T_ev - tol - e["d_max"] / C_SOUND - 120, T_ev + tol + 120)
            if (trw["valid"] & np.isfinite(trw["lat"])).sum() < 2: continue
            tc, dc, tau, v, _ = cpa_many(trw, plat, plon, pel)
            m = (np.abs(tau - T_ev) <= tol) & (dc >= e["d_min"]) & (dc <= e["d_max"]) & (v >= e["v_min"]) & (v <= e["v_max"])
            if m.any():
                recs.append(np.c_[np.full(m.sum(), f), plat[m], plon[m], pel[m], tc[m], dc[m], tau[m], v[m]])
        return np.concatenate(recs) if recs else np.zeros((0, 8))

    def score(R, pp=p):
        sc = np.full(R.shape[0], -1e18); bd = np.zeros(R.shape[0]); bf = np.zeros(R.shape[0])
        ids = R[:, 0].astype(np.int64)
        for f in np.unique(ids):
            idx = np.flatnonzero(ids == f)
            if centred:
                # per-point alignment: model curve sampled around that point's own delayed CPA
                dt0 = e["t0"] - (R[idx, 6] - rec_start_abs)
                G = _g(store, f, pp, R[idx, 1], R[idx, 2], R[idx, 3], (R[idx, 6] - e["t0"])[:, None] + tcl[None, :])
                s_, d_, f_ = _batch_line_c(tl, fl, tcl, G, np.zeros(idx.size), dts_c, e["f0"] * f0rel, fdt_c, ffr)
                d_ = d_ + dt0
                s_ = np.where(np.abs(d_) <= tol + 1.0, s_, -1e18)   # total clock offset must stay inside the clock uncertainty
            else:
                G = _g(store, f, pp, R[idx, 1], R[idx, 2], R[idx, 3], np.broadcast_to(Tabs, (idx.size, Tabs.size)).copy())
                s_, d_, f_ = _batch_line(tl, fl, tcurve, G, dts, e["f0"] * f0rel, fdt, ffr)
            sc[idx] = s_; bd[idx] = d_; bf[idx] = f_
        return sc, bd, bf

    coarse = p.single_coarse_m if p.single_coarse_m and p.single_coarse_m > p.grid_m else None
    R = records(fids, coarse or p.grid_m)
    log(f"  candidate (point, flight) records: {R.shape[0]} on a {coarse or p.grid_m:.0f} m grid ({time.time()-T0:.0f}s)")
    if R.shape[0] == 0: return dict(status="no candidates")
    if progress: progress(0.3, f"scoring {R.shape[0]} points")
    sc, bd, bf = score(R)
    if coarse:
        keep = np.argsort(-sc)[:p.single_keep]; keep = keep[sc[keep] > -1e17]
        h = coarse / 2.0; boxes = {}
        for i in keep:
            la, lo = R[i, 1], R[i, 2]; dlo = h / (111320.0 * math.cos(math.radians(la)))
            boxes.setdefault(int(R[i, 0]), []).append((la - h / 111320.0, la + h / 111320.0, lo - dlo, lo + dlo))
        if progress: progress(0.6, f"refining {keep.size} coarse cells")
        R2 = records(list(boxes), p.grid_m, boxes)
        log(f"  refine: best {keep.size} coarse cells -> {R2.shape[0]} records on {p.grid_m:.0f} m ({time.time()-T0:.0f}s)")
        if R2.shape[0]:
            s2, d2, f2 = score(R2)
            R = np.r_[R, R2]; sc = np.r_[sc, s2]; bd = np.r_[bd, d2]; bf = np.r_[bf, f2]
    df = pl.DataFrame(dict(flightid_1=R[:, 0].astype(np.int64), lat=R[:, 1], lon=R[:, 2], elev_m=R[:, 3],
                           cpa_time_raw_1=R[:, 4], cpa_distance_m_1=R[:, 5], cpa_time_utc_1=R[:, 6],
                           seg_speed_mps_1=R[:, 7], total_score=sc, event1_score=sc, dt_s=bd, f0_1=bf))
    df = df.filter(pl.col("total_score") > -1e17).sort("total_score", descending=True)
    if p.wind == "fit" and len(df):
        def rescore(sub, pp):
            Rs = np.c_[sub["flightid_1"].to_numpy(), sub["lat"].to_numpy(), sub["lon"].to_numpy(), sub["elev_m"].to_numpy(),
                       sub["cpa_time_raw_1"].to_numpy(), sub["cpa_distance_m_1"].to_numpy(), sub["cpa_time_utc_1"].to_numpy(),
                       sub["seg_speed_mps_1"].to_numpy()]
            s_, d_, f_ = score(Rs, pp)
            return sub.with_columns(pl.Series("total_score", s_), pl.Series("event1_score", s_), pl.Series("dt_s", d_), pl.Series("f0_1", f_))
        df = wind_fit(df, 1, rescore, p, log).filter(pl.col("total_score") > -1e17)
    log(f"  scored {len(df)} points in {time.time()-T0:.0f}s; max {df['total_score'].max():.2f}")
    return dict(status="ok", mode="single", scored=df, combos=best_per_combo(df, 1), n=len(df),
                stats=dict(n=len(df), min=float(df["total_score"].min()), max=float(df["total_score"].max()),
                           mean=float(df["total_score"].mean())), event=e, rec_start_abs=rec_start_abs,
                params=asdict(p), elapsed_s=time.time() - T0)

def corridor_pids(la, lo, rmax, step):
    """Grid points within rmax (horizontal) of the samples."""
    lat0 = float(np.mean(la)); lon0 = float(np.mean(lo))
    x, y = enu(la, lo, lat0, lon0)
    tree = cKDTree(np.c_[x, y])
    pids = grid_in_box(la.min() - rmax / 111320, la.max() + rmax / 111320,
                       lo.min() - rmax / (111320 * math.cos(math.radians(lat0))), lo.max() + rmax / (111320 * math.cos(math.radians(lat0))), step)
    pla, plo = pid_decode(pids, step)
    px, py = enu(pla, plo, lat0, lon0)
    d, _ = tree.query(np.c_[px, py], distance_upper_bound=rmax + step)
    return pids[np.isfinite(d)]

# ------------------------------------------------------------------ heat maps for a chosen combo
def heatmap(store, spec, ev, combo, center, p, half_km=2.0, step_m=25.0):
    """Finer grid around a combo's best point (SPEC §6): event-1 only, event-2 only, combined (locked)."""
    lat0, lon0 = center
    dl = half_km * 1000 / 111320.0; dlo = dl / math.cos(math.radians(lat0))
    pids = grid_in_box(lat0 - dl, lat0 + dl, lon0 - dlo, lon0 + dlo, step_m)
    la, lo = pid_decode(pids, step_m); el = elevation(la, lo)
    N = len(ev)
    combos = np.tile(np.asarray(combo)[None, :], (la.size, 1))
    sc, bdt, bf, _, _, _ = score_points(store, spec, ev, la, lo, el, combos, p)
    layers = {"combined": sc.sum(1)}
    # each event scored alone with its own dt/f0 sweep
    for k in range(N):
        sck, _, _, _, _, _ = score_points(store, spec, [ev[k]] + [ev[j] for j in range(N) if j != k], la, lo, el,
                                       np.c_[combos[:, k], np.delete(combos, k, 1)], p)
        layers[f"event{k+1}"] = sck[:, 0]
    return dict(lat=la, lon=lo, layers=layers, step_m=step_m)
