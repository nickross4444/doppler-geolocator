"""Doppler physics (SPEC §3.2, §5.1)."""
import numpy as np
from . import C_SOUND
from .geo import enu

def straight_line_curve(t, f0, v, d, t0, c=C_SOUND):
    """The measurement tool's constant-velocity curve: f = f0 c/(c+v_r), v_r = v^2 (t-t0)/sqrt(d^2+v^2(t-t0)^2).
    (emission/reception distinction ignored, as in the on-screen 'Doppler Curve Fit' sliders)."""
    tau = np.asarray(t, float) - t0
    vr = v * v * tau / np.sqrt(d * d + (v * tau) ** 2)
    return f0 * c / (c + vr)

def fit_readouts(f0, v, d, c=C_SOUND):
    """On-screen readouts: f_pre, f_post, max RoC (Hz/s at CPA)."""
    return dict(f_pre=f0 * c / (c - v), f_post=f0 * c / (c + v), max_roc=f0 * v * v / (c * d))

def track_enu(tr, lat0, lon0, elev0):
    x, y = enu(tr["lat"], tr["lon"], lat0, lon0)
    z = tr["alt_m"] - elev0
    th = np.radians(tr["trk"])
    vx = tr["gs"] * np.sin(th); vy = tr["gs"] * np.cos(th); vz = np.nan_to_num(tr["vs"])
    return np.c_[x, y, z], np.c_[vx, vy, vz]

def cpa(tr, lat, lon, elev, c=C_SOUND):
    """Closest point of approach (3D slant) of a 1 s track to an observer, refined by parabolic
    interpolation. Returns (cpa_time, slant_dist, delayed_cpa_time, speed_at_cpa, index)."""
    X, V = track_enu(tr, lat, lon, elev)
    r = np.sqrt((X ** 2).sum(1))
    if not np.isfinite(r).any(): return None
    i = int(np.nanargmin(r))
    t_c = tr["t"][i]; rc = r[i]
    if 0 < i < r.size - 1:
        a, b, cc = r[i - 1], r[i], r[i + 1]
        den = a - 2 * b + cc
        if den > 0:
            off = 0.5 * (a - cc) / den
            t_c = tr["t"][i] + off; rc = b - 0.25 * (a - cc) * off
    return t_c, rc, t_c + rc / c, float(tr["gs"][i]), i

def emission_time(t_r, X, t, c=C_SOUND, iters=8):
    """Solve t_r = t_e + |X(t_e)|/c (observer at origin) by fixed-point iteration on the 1 s track."""
    t_e = np.asarray(t_r, float).copy()
    for _ in range(iters):
        px = np.interp(t_e, t, X[:, 0]); py = np.interp(t_e, t, X[:, 1]); pz = np.interp(t_e, t, X[:, 2])
        t_e = t_r - np.sqrt(px * px + py * py + pz * pz) / c
    return t_e

def model_curve(tr, lat, lon, elev, t_r, f0, c=C_SOUND, delayed=True):
    """Frequency heard at reception times t_r (absolute s) from the real flight geometry.
    delayed=True: emission-time radial velocity (correct); False: 'Position' mode (wrong, for teaching)."""
    X, V = track_enu(tr, lat, lon, elev)
    t = tr["t"]
    te = emission_time(t_r, X, t, c) if delayed else np.asarray(t_r, float)
    P = np.c_[[np.interp(te, t, X[:, k]) for k in range(3)]].T
    W = np.c_[[np.interp(te, t, V[:, k]) for k in range(3)]].T
    r = np.sqrt((P ** 2).sum(1))
    vr = (P * W).sum(1) / np.maximum(r, 1e-6)          # +ve receding
    f = f0 * c / (c + vr)
    inside = (te >= t[0]) & (te <= t[-1])
    f[~inside] = np.nan
    return f, te, vr, r

def worked_example():
    """SPEC §5.1 on-screen step 11/11: t_r=25.00 s -> t_e=16.910 s, aircraft (2367,1264,1020) m,
    V=(140,16.8,0) m/s, r=2775 m, v_r=121.16 m/s, f=130*343/(343+121.16)=96.07 Hz.
    The on-screen (x,y,z) is in the demo's map frame, not observer-centred: r=2775 and v_r=121.16 are
    jointly consistent only with the observer at ~(117.15, -0.28, 0) m in that frame (solved from the two
    displayed values), so we place it there and run the same functions the search uses."""
    obs = np.array([117.149, -0.2765, 0.0])
    X = np.array([2367.0, 1264.0, 1020.0]) - obs; V = np.array([140.0, 16.8, 0.0])
    # straight track through X at t_e with velocity V, sampled at 1 s, then solve the retarded time
    t = np.arange(0.0, 40.0, 1.0); t_e_true = 16.910
    Xt = X[None, :] + (t[:, None] - t_e_true) * V[None, :]
    te = float(emission_time(np.array([25.0]), Xt, t)[0])
    P = np.array([np.interp(te, t, Xt[:, k]) for k in range(3)])
    r = float(np.linalg.norm(P)); vr = float(P @ V / r)
    return dict(t_e=te, r=r, v_r=vr, f=130.0 * C_SOUND / (C_SOUND + vr))

def retarded_line_g(t, v, d, t0, c=C_SOUND):
    """Constant-velocity straight pass WITH propagation delay (the drag tool's 'propagation delay' on):
    ratio g = c/(c+v_r(t_e)) at reception time t; t0 = delayed CPA (reception time of the CPA sound).
    Closed form: with s = t - t_c, u = t_e - t_c:  (c^2-v^2) u^2 - 2 c^2 s u + c^2 s^2 - d^2 = 0."""
    t_c = t0 - d / c
    s = np.asarray(t, float) - t_c
    a = c * c - v * v
    disc = np.maximum(c ** 4 * s * s - a * (c * c * s * s - d * d), 0.0)
    u = (c * c * s - np.sqrt(disc)) / a
    r = np.sqrt(d * d + (v * u) ** 2)
    vr = v * v * u / r
    return c / (c + vr)

def retarded_line_curve(t, f0, v, d, t0, c=C_SOUND):
    return f0 * retarded_line_g(t, v, d, t0, c)

# ---------------------------------------------------------------- batch (numba) exact model for many observers
import numba as _nb
from .geo import R_EARTH as _RE

def track_ecef(tr):
    """Track on a uniform 1 s grid -> ECEF positions (spherical Earth, radius+alt) and velocities from
    gs/track/vs rotated from local ENU. Same geometry as the per-point model."""
    la = np.radians(tr["lat"]); lo = np.radians(tr["lon"]); R = _RE + tr["alt_m"]
    cl, sl, co, so = np.cos(la), np.sin(la), np.cos(lo), np.sin(lo)
    X = np.c_[R * cl * co, R * cl * so, R * sl]
    th = np.radians(tr["trk"])
    ve = tr["gs"] * np.sin(th); vn = tr["gs"] * np.cos(th); vu = np.nan_to_num(tr["vs"])
    # ENU -> ECEF
    V = np.c_[-so * ve - sl * co * vn + cl * co * vu, co * ve - sl * so * vn + cl * so * vu, cl * vn + sl * vu]
    ok = tr["valid"] & np.isfinite(X).all(1) & np.isfinite(V).all(1)
    return X, V, ok

def observers_ecef(lat, lon, elev):
    la = np.radians(lat); lo = np.radians(lon); R = _RE + np.asarray(elev, float)
    return np.c_[R * np.cos(la) * np.cos(lo), R * np.cos(la) * np.sin(lo), R * np.sin(la)]

@_nb.njit(parallel=True, cache=True, fastmath=True)
def g_batch(t0, X, V, ok, O, T, c, iters, W, wmode):
    """Exact emission-time Doppler ratio g=c/(c+v_r) for Q observers O (Q,3) at reception times T (Q,S).
    Track: uniform 1 s samples starting t0. NaN where emission time leaves the track or hits invalid data.
    Wind (optional, W = ECEF wind vector m/s, constant for the pass): wmode 0 = none (the video's method);
    1 = 'air_los': source velocity taken relative to the air, g = c/(c + (V-W).m) (observer's motion through the air ignored);
    2 = 'full': moving medium, sound travels at c_eff = c - W.m along the ray (m = unit observer->source), g = c_eff/(c_eff + V.m)
    and the propagation delay uses c_eff. In 'full' the wind cancels to first order (stationary observer)."""
    Q, S = T.shape; n = X.shape[0]
    G = np.full((Q, S), np.nan)
    for q in _nb.prange(Q):
        for s in range(S):
            te = T[q, s]
            good = True
            for it in range(iters):
                x = te - t0
                if x < 0 or x > n - 1.000001: good = False; break
                i = int(x); a = x - i
                if not (ok[i] and ok[i + 1]): good = False; break
                dx = X[i, 0] * (1 - a) + X[i + 1, 0] * a - O[q, 0]
                dy = X[i, 1] * (1 - a) + X[i + 1, 1] * a - O[q, 1]
                dz = X[i, 2] * (1 - a) + X[i + 1, 2] * a - O[q, 2]
                r = np.sqrt(dx * dx + dy * dy + dz * dz)
                ce = c
                if wmode == 2: ce = c - (dx * W[0] + dy * W[1] + dz * W[2]) / r
                te = T[q, s] - r / ce
            if not good: continue
            x = te - t0
            if x < 0 or x > n - 1.000001: continue
            i = int(x); a = x - i
            dx = X[i, 0] * (1 - a) + X[i + 1, 0] * a - O[q, 0]
            dy = X[i, 1] * (1 - a) + X[i + 1, 1] * a - O[q, 1]
            dz = X[i, 2] * (1 - a) + X[i + 1, 2] * a - O[q, 2]
            vx = V[i, 0] * (1 - a) + V[i + 1, 0] * a
            vy = V[i, 1] * (1 - a) + V[i + 1, 1] * a
            vz = V[i, 2] * (1 - a) + V[i + 1, 2] * a
            r = np.sqrt(dx * dx + dy * dy + dz * dz)
            vr = (dx * vx + dy * vy + dz * vz) / r
            wr = (dx * W[0] + dy * W[1] + dz * W[2]) / r
            if wmode == 1: G[q, s] = c / (c + vr - wr)
            elif wmode == 2: G[q, s] = (c - wr) / (c - wr + vr)
            else: G[q, s] = c / (c + vr)
    return G

WIND_MODES = {"off": 0, "air_los": 1, "full": 2}

def wind_ecef(lat, lon, u_east, v_north):
    la = np.radians(lat); lo = np.radians(lon)
    return np.array([-np.sin(lo) * u_east - np.sin(la) * np.cos(lo) * v_north,
                     np.cos(lo) * u_east - np.sin(la) * np.sin(lo) * v_north,
                     np.cos(la) * v_north])

def model_g_many(tr, lat, lon, elev, T, c=C_SOUND, iters=8, wind=None, wind_model="full"):
    """wind: None or (u_east, v_north) m/s, applied as a constant vector for this pass (see g_batch)."""
    X, V, ok = track_ecef(tr)
    O = observers_ecef(np.atleast_1d(lat), np.atleast_1d(lon), np.atleast_1d(elev))
    T = np.atleast_2d(np.asarray(T, float))
    if T.shape[0] == 1 and O.shape[0] > 1: T = np.repeat(T, O.shape[0], 0)
    if wind is None or (wind[0] == 0 and wind[1] == 0): W = np.zeros(3); wm = 0
    else:
        W = wind_ecef(float(np.nanmean(np.atleast_1d(lat))), float(np.nanmean(np.atleast_1d(lon))), wind[0], wind[1]); wm = WIND_MODES[wind_model]
    return g_batch(float(tr["t"][0]), X, V, ok, O, T, c, iters, W, wm)
