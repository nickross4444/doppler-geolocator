"""Audio ingest, whitened spectrogram, and automatic faint-Doppler detection (SPEC §3).

Spectrogram used for display AND scoring (open question Q2 in SPEC; our documented choice):
  mono, resampled to `sr` (default 8 kHz), Hann STFT, nperseg = sr * win_s (default 1.0 s -> 1 Hz bins),
  hop 0.25 s. Power in dB. "Brightness" for scoring is a whitened z-score:
      Z = (dB - rolling_median_t(dB)) / (1.4826 * rolling_MAD_t(dB))   per frequency bin, 120 s blocks
  which removes stationary hum/tones and makes scores comparable across recordings
  (mean Z along a curve ~ 0 for noise, can be negative; ~5-15 on a clear ridge, like the video's 8-10 scores).
"""
import os, subprocess, json, hashlib, math
import numpy as np
from scipy import signal, ndimage
import numba as nb
from . import C_SOUND
from .physics import retarded_line_g, retarded_line_curve

def extract_audio(src, dst_wav, sr=8000):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", str(sr),
                    "-c:a", "pcm_s16le", dst_wav], check=True)
    return dst_wav

def extract_playback(src, dst_mp3):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-b:a", "96k", dst_mp3], check=True)
    return dst_mp3

def read_wav(path):
    from scipy.io import wavfile
    sr, x = wavfile.read(path)
    if x.ndim > 1: x = x.mean(1)
    x = x.astype(np.float32)
    if np.issubdtype(x.dtype, np.integer) or np.abs(x).max() > 2: x /= 32768.0
    return sr, x

class Spectrogram:
    def __init__(self, t, f, db, z, params):
        self.t, self.f, self.db, self.z, self.params = t, f, db, z, params
        self.dt = float(t[1] - t[0]); self.df = float(f[1] - f[0])

    def save(self, path):
        np.savez_compressed(path, t=self.t, f=self.f, db=self.db.astype(np.float16), z=self.z.astype(np.float16),
                            params=json.dumps(self.params))
    @classmethod
    def load(cls, path):
        a = np.load(path)
        return cls(a["t"], a["f"], a["db"].astype(np.float32), a["z"].astype(np.float32), json.loads(str(a["params"])))

def compute_spectrogram(wav, win_s=1.0, hop_s=0.25, fmin=20.0, fmax=1500.0, block_s=120.0):
    sr, x = read_wav(wav)
    nper = int(round(sr * win_s)); hop = int(round(sr * hop_s))
    f, t, S = signal.stft(x, fs=sr, window="hann", nperseg=nper, noverlap=nper - hop, boundary=None, padded=False)
    band = (f >= fmin) & (f <= min(fmax, sr / 2))
    P = (np.abs(S[band]) ** 2).astype(np.float32)
    db = 10 * np.log10(P + 1e-12)
    t = t.astype(np.float64); f = f[band]
    z = whiten(db, hop_s, block_s)
    return Spectrogram(t, f, db.T.copy(), z.T.copy(), dict(sr=sr, win_s=win_s, hop_s=hop_s, fmin=fmin, fmax=fmax,
                                                          block_s=block_s))

def median_removed(db, hop_s, block_s):
    """dB minus the per-frequency block median (no MAD scaling): brightness in dB above the local background."""
    F, T = db.shape
    nb_ = max(1, int(round(T * hop_s / block_s)))
    edges = np.linspace(0, T, nb_ + 1).astype(int); med = np.empty((F, nb_), np.float32); cen = np.empty(nb_)
    for i in range(nb_):
        med[:, i] = np.median(db[:, edges[i]:edges[i + 1]], axis=1); cen[i] = 0.5 * (edges[i] + edges[i + 1])
    idx = np.arange(T)
    M = med[:, :1] if nb_ == 1 else np.stack([np.interp(idx, cen, med[k]) for k in range(F)])
    return (db - M).astype(np.float32)

def whiten(db, hop_s, block_s):
    """Per-frequency robust z-score against block-wise (block_s) median/MAD, linearly interpolated in time."""
    F, T = db.shape
    nb_ = max(1, int(round(T * hop_s / block_s)))
    edges = np.linspace(0, T, nb_ + 1).astype(int)
    med = np.empty((F, nb_), np.float32); mad = np.empty((F, nb_), np.float32); cen = np.empty(nb_)
    for i in range(nb_):
        blk = db[:, edges[i]:edges[i + 1]]
        m = np.median(blk, axis=1); med[:, i] = m
        mad[:, i] = np.median(np.abs(blk - m[:, None]), axis=1) * 1.4826 + 1e-3
        cen[i] = 0.5 * (edges[i] + edges[i + 1])
    idx = np.arange(T)
    if nb_ == 1:
        M = med[:, :1]; D = mad[:, :1]
    else:
        M = np.stack([np.interp(idx, cen, med[k]) for k in range(F)]); D = np.stack([np.interp(idx, cen, mad[k]) for k in range(F)])
    z = (db - M) / D
    return np.clip(z, -6, 30).astype(np.float32)

# ---------------------------------------------------------------- scoring kernels (used by search)
@nb.njit(cache=True, fastmath=True)
def _bilinear(Z, t0, dt, f0, df, tt, ff):
    T, F = Z.shape
    x = (tt - t0) / dt; y = (ff - f0) / df
    if not (x >= 0 and x < T - 1 and y >= 0 and y < F - 1): return np.nan
    i = int(x); j = int(y); a = x - i; b = y - j
    return (Z[i, j] * (1 - a) * (1 - b) + Z[i + 1, j] * a * (1 - b) + Z[i, j + 1] * (1 - a) * b + Z[i + 1, j + 1] * a * b)

@nb.njit(cache=True, fastmath=True)
def curve_score(Z, zt0, zdt, zf0, zdf, t_rec, g, f0, harmonics, agg=0):
    """Mean whitened brightness under the curve f(t)=f0*g(t) (+ optional harmonics). NaN samples skipped.
    agg=1: SUM over the samples instead of the mean (the video's wording 'sum of brightness values the curve covers');
    samples where the model curve is undefined (outside the track) then contribute 0."""
    s = 0.0; n = 0
    for i in range(t_rec.size):
        if not np.isfinite(g[i]): continue
        for h in range(1, harmonics + 1):
            v = _bilinear(Z, zt0, zdt, zf0, zdf, t_rec[i], f0 * g[i] * h)
            if np.isfinite(v):
                s += v; n += 1
    if n == 0: return -1e9
    return s if agg == 1 else s / n

@nb.njit(cache=True, fastmath=True)
def sweep_dt_f0(Z, zt0, zdt, zf0, zdf, t_rec, g, dts, f0s, harmonics):
    best = -1e18; bdt = 0.0; bf = 0.0
    for a in range(dts.size):
        tt = t_rec + dts[a]
        for b in range(f0s.size):
            s = curve_score(Z, zt0, zdt, zf0, zdf, tt, g, f0s[b], harmonics)
            if s > best: best = s; bdt = dts[a]; bf = f0s[b]
    return best, bdt, bf

@nb.njit(cache=True, fastmath=True)
def sweep_f0(Z, zt0, zdt, zf0, zdf, t_rec, g, f0s, harmonics):
    best = -1e18; bf = 0.0
    for b in range(f0s.size):
        s = curve_score(Z, zt0, zdt, zf0, zdf, t_rec, g, f0s[b], harmonics)
        if s > best: best = s; bf = f0s[b]
    return best, bf

@nb.njit(cache=True, fastmath=True)
def line_distance_sweep(t_line, f_line, t_curve, g, dts, f0s):
    """Single-flight mode: -mean |f_model - f_line| (Hz) over the traced line; sweep dt and f0.
    t_curve/g: model curve sampled on a fine reception-time grid (sorted)."""
    best = -1e18; bdt = 0.0; bf = 0.0
    for a in range(dts.size):
        for b in range(f0s.size):
            s = 0.0; n = 0
            for i in range(t_line.size):
                tq = t_line[i] - dts[a]
                if tq < t_curve[0] or tq > t_curve[-1]: continue
                k = np.searchsorted(t_curve, tq)
                if k == 0: k = 1
                w = (tq - t_curve[k - 1]) / (t_curve[k] - t_curve[k - 1])
                gv = g[k - 1] * (1 - w) + g[k] * w
                if not np.isfinite(gv): continue
                s += abs(f0s[b] * gv - f_line[i]); n += 1
            if n >= max(3, t_line.size // 2):
                sc = -s / n
                if sc > best: best = sc; bdt = dts[a]; bf = f0s[b]
    return best, bdt, bf

# ---------------------------------------------------------------- automatic detection
def straight_g(tau, v, d, c=C_SOUND):
    vr = v * v * tau / np.sqrt(d * d + (v * tau) ** 2)
    return c / (c + vr)

@nb.njit(parallel=True, cache=True, fastmath=True)
def _template_map(H, offs_t, offs_f, out):
    T, F = H.shape
    n = offs_t.size
    for i in nb.prange(T):
        for j in range(F):
            s = 0.0; c = 0
            for k in range(n):
                ii = i + offs_t[k]; jj = j + offs_f[k]
                if ii >= 0 and ii < T and jj >= 0 and jj < F:
                    s += H[ii, jj]; c += 1
            out[i, j] = s / c if c > n // 2 else -10.0

def detect_events(sp, fmin=50.0, fmax=1200.0, half_window_s=40.0, harmonics=3, min_conf=6.0, max_events=30, clip_z=3.0,
                  vs=(40, 55, 70, 85, 100, 120, 140, 160), ds=(500, 900, 1500, 2500, 4000, 6000, 9000), min_transit_s=4.0, frame_norm=True,
                  harmonic_fit=False):
    """Matched-filter bank for faint Doppler curves buried in noise.
    1. whitened Z (removes stationary hum) -> log-frequency axis (0.4 % bins) at 1 s frames, 3-bin max in f
    2. harmonic stacking: H = mean_h Z(log f + log h), h=1..harmonics (a prop/rotor tone is a harmonic comb)
    3. straight-line Doppler templates (v, d): in log-f a template is a fixed path, so the matched score for
       every (t0, f0) is a shifted average  M(t0, log f0) = mean_tau H(t0+tau, log f0 + log g_vd(tau))
    4. confidence = (M_doppler - M_flat)/sigma_null : excess over a constant-frequency (stationary) template,
       sigma_null = robust std of that excess over the whole map
    5. NMS in (t0 +-30 s, f0 +-6 %), then per-candidate continuous refinement + ridge tracing."""
    tstep = 1.0
    k = max(1, int(round(tstep / sp.dt)))
    Z = sp.z
    if frame_norm:   # remove broadband transients (wind, clicks, footsteps): subtract each frame's median over f
        Z = Z - np.median(Z, axis=1, keepdims=True)
    nT = Z.shape[0] // k
    Zc = Z[:nT * k].reshape(nT, k, -1).mean(1)
    Zc = ndimage.maximum_filter1d(Zc, 3, axis=1)
    r = 0.004
    lf = np.arange(math.log(fmin), math.log(fmax), r)
    fq = np.exp(lf)
    L = np.stack([np.interp(fq, sp.f, Zc[i], left=np.nan, right=np.nan) for i in range(nT)])
    Hs = np.zeros_like(L); cnt = np.zeros_like(L)
    for h in range(1, harmonics + 1):
        sh = int(round(math.log(h) / r))
        if sh >= L.shape[1]: break
        part = np.full_like(L, np.nan); part[:, :L.shape[1] - sh] = L[:, sh:]
        m = np.isfinite(part); Hs[m] += part[m]; cnt[m] += 1
    H = np.where(cnt > 0, Hs / np.maximum(cnt, 1), 0.0).astype(np.float32)
    H1 = np.nan_to_num(L).astype(np.float32)
    taus = np.arange(-half_window_s, half_window_s + 1e-9, tstep)
    offs_t = np.round(taus / tstep).astype(np.int64)
    tmpl = [(v, d) for v in vs for d in ds if d / v >= min_transit_s]   # physically plausible passes
    conf = None
    # two maps: harmonic-stacked (prop/rotor combs) and fundamental-only (single tones, e.g. a fan line)
    for Hx in ((H, H1) if harmonics > 1 else (H1,)):
        Hx = np.clip(np.nan_to_num(Hx), -2.0, clip_z)   # clipping: short loud transients (birds, clicks) can't dominate
        flat = np.empty_like(Hx); _template_map(Hx, offs_t, np.zeros_like(offs_t), flat)
        best = np.full_like(Hx, -10.0); arg = np.zeros(Hx.shape, np.int32)
        out = np.empty_like(Hx)
        for ti, (v, d) in enumerate(tmpl):
            offs_f = np.round(np.log(straight_g(taus, v, d)) / r).astype(np.int64)
            _template_map(Hx, offs_t, offs_f, out)
            m = out > best; best[m] = out[m]; arg[m] = ti
        exc = best - flat
        med = np.median(exc, axis=0); sig = 1.4826 * np.median(np.abs(exc - med), axis=0) + 1e-3
        cf = (exc - med) / sig            # per-frequency null distribution (noise differs strongly with f)
        if conf is None:
            conf, carg, cbest, cflat = cf, arg, best, flat
        else:
            m = cf > conf
            conf = np.where(m, cf, conf); carg = np.where(m, arg, carg)
            cbest = np.where(m, best, cbest); cflat = np.where(m, flat, cflat)
    arg, best, flat = carg, cbest, cflat
    # NMS
    cands = []
    c2 = conf.copy()
    nt_w = int(30 / tstep); nf_w = int(0.06 / r)
    for _ in range(max_events * 3):
        i, j = np.unravel_index(np.argmax(c2), c2.shape)
        if c2[i, j] < min_conf: break
        v, d = tmpl[arg[i, j]]
        cands.append(dict(t0=float(i * tstep * 1.0 + sp.t[0]), f0=float(fq[j]), v=float(v), d=float(d),
                          conf=float(conf[i, j]), template_score=float(best[i, j]), flat_score=float(flat[i, j])))
        c2[max(0, i - nt_w):i + nt_w + 1, max(0, j - nf_w):j + nf_w + 1] = -1e9
        # suppress harmonics of the same pass
        for h in (2, 3, 4):   # only higher harmonics: a spurious high candidate must not hide a real lower curve
            jj = j + int(round(math.log(h) / r))
            if 0 <= jj < c2.shape[1]:
                c2[max(0, i - nt_w):i + nt_w + 1, max(0, jj - nf_w):jj + nf_w + 1] = -1e9
        if len(cands) >= max_events: break
    out_ev = []
    for c in cands:
        try:
            out_ev.append(refine_event(sp, c, half_window_s=half_window_s, harmonics=harmonics, harmonic_fit=harmonic_fit))
        except Exception as e:
            c["error"] = str(e); out_ev.append(c)
    out_ev = [e for e in out_ev if len(e.get("line", [])) >= 12]
    # group tone components of the same pass (t0 within 15 s) -> 'pass_id'
    out_ev.sort(key=lambda e: -e["conf"])
    groups = []
    for e in out_ev:
        for gi, g in enumerate(groups):
            if abs(g - e["t0"]) < 15: e["pass_id"] = gi; break
        else:
            e["pass_id"] = len(groups); groups.append(e["t0"])
        e["primary"] = not any(o is not e and o.get("pass_id") == e["pass_id"] and o["conf"] > e["conf"] for o in out_ev)
    return out_ev

def _score_params(sp, p, taus_abs, harmonics, retarded=True):
    f0, v, d, t0 = p
    if v <= 5 or v > 330 or d < 50 or f0 <= 5: return -1e9
    g = retarded_line_g(taus_abs, v, d, t0) if retarded else straight_g(taus_abs - t0, v, d)
    return curve_score(sp.z, sp.t[0], sp.dt, sp.f[0], sp.df, taus_abs, g, f0, harmonics)

V_REL, D_LO, D_HI = 0.12, 0.6, 1.45

def refine_event(sp, c, half_window_s=40.0, harmonics=3, drop=0.15, harmonic_fit=False):
    """Continuous fit of (f0, v, d, t0) maximising whitened brightness, then MIN/MAX ranges where the
    score stays within `drop` (fraction) of the best (the author's 'generous range'; BEST = midpoint).
    harmonic_fit=True (optional, default off; not the video's method): if the line belongs to a visible harmonic comb,
    replace (f0, v, d, t0) with the joint fit over every harmonic (dopp/harmonics.py; single-line values kept)."""
    from scipy.optimize import minimize
    taus = np.arange(c["t0"] - half_window_s, c["t0"] + half_window_s, sp.dt)
    taus = taus[(taus > sp.t[0]) & (taus < sp.t[-1])]
    best = None
    for h in (1,):
        x0 = np.array([c["f0"], c["v"], c["d"], c["t0"]])
        fun = lambda p: -_score_params(sp, p, taus, harmonics)
        res = minimize(fun, x0, method="Nelder-Mead",
                       options=dict(xatol=0.05, fatol=1e-3, maxiter=600, initial_simplex=np.array([
                           x0, x0 + [x0[0] * 0.01, 0, 0, 0], x0 + [0, 15, 0, 0], x0 + [0, 0, x0[2] * 0.4, 0], x0 + [0, 0, 0, 2.0]])))
        if best is None or res.fun < best.fun: best = res
    f0, v, d, t0 = best.x; s_best = -best.fun
    thr = s_best * (1 - drop)
    def prange(idx, grid):
        ok = []
        for val in grid:
            p = best.x.copy(); p[idx] = val
            if _score_params(sp, p, taus, harmonics) >= thr: ok.append(val)
        return (min(ok), max(ok)) if ok else (best.x[idx], best.x[idx])
    vmin, vmax = prange(1, np.linspace(max(10, v * 0.6), min(320, v * 1.5), 61))
    dmin, dmax = prange(2, np.geomspace(max(60, d * 0.3), d * 3.0, 61))
    # 'pretty generous range ... so that we don't accidentally filter anything out' [T 09:49]:
    # widen to at least v*(1 +- V_REL) and d*[D_LO, D_HI] (ADS-B gives ground speed; wind ~ +-10 m/s)
    vmin = min(vmin, v * (1 - V_REL)); vmax = max(vmax, v * (1 + V_REL))
    dmin = min(dmin, d * D_LO); dmax = max(dmax, d * D_HI)
    tmin, tmax = prange(3, np.linspace(t0 - 6, t0 + 6, 121))
    line = trace_ridge(sp, f0, v, d, t0, half_window_s)
    # least-squares fit of the retarded constant-velocity model to the traced ridge (sub-bin accuracy)
    lsq = None
    if len(line) >= 12:
        from scipy.optimize import least_squares
        L = np.asarray(line)
        fun = lambda q: retarded_line_curve(L[:, 0], q[0], q[1], q[2], q[3]) - L[:, 1]
        try:
            rr = least_squares(fun, [f0, v, d, t0], bounds=([5, 10, 50, t0 - 20], [5000, 320, 40000, t0 + 20]), loss="soft_l1", f_scale=1.0)
            if rr.success:
                J = rr.jac; cov = np.linalg.pinv(J.T @ J) * max(1e-6, np.mean(rr.fun ** 2))
                lsq = dict(x=rr.x, sd=np.sqrt(np.clip(np.diag(cov), 0, None)), rms=float(np.sqrt(np.mean(rr.fun ** 2))))
                f0, v, d, t0 = rr.x
        except Exception:
            pass
    out = dict(c)
    out.update(f0=float(f0), v=float(v), d=float(d), t0=float(t0), score=float(s_best),
               v_min=float(vmin), v_max=float(vmax), v_best=float(0.5 * (vmin + vmax)),
               d_min=float(dmin), d_max=float(dmax), d_best=float(0.5 * (dmin + dmax)),
               t0_min=float(tmin), t0_max=float(tmax), t0_sigma=float(max(0.25, (tmax - tmin) / 4)),
               line=line, source="auto", model="retarded")
    if lsq is not None:
        out.update(fit_rms_hz=lsq["rms"], t0_sigma=float(max(0.25, lsq["sd"][3])))
    from .physics import fit_readouts
    out.update(fit_readouts(f0, v, d))
    # final generous ranges about the least-squares values
    out["v_min"] = float(min(out["v_min"], v * (1 - V_REL))); out["v_max"] = float(max(out["v_max"], v * (1 + V_REL)))
    if not (out["v_min"] <= v <= out["v_max"]): out["v_min"], out["v_max"] = v * (1 - V_REL), v * (1 + V_REL)
    if not (out["d_min"] <= d <= out["d_max"]): out["d_min"], out["d_max"] = d * D_LO, d * D_HI
    out["v_best"] = 0.5 * (out["v_min"] + out["v_max"]); out["d_best"] = 0.5 * (out["d_min"] + out["d_max"])
    if harmonic_fit:
        from .harmonics import harmonic_refit
        try: out = harmonic_refit(sp, out)
        except Exception as ex: out["comb_error"] = str(ex)
    return out

def trace_ridge(sp, f0, v, d, t0, half_window_s=40.0, rel=0.03, zmin=1.5):
    return _trace(sp, lambda t: f0 * retarded_line_g(t, v, d, t0), t0, half_window_s, rel, zmin)

def _trace(sp, fmodel, t0, half_window_s, rel, zmin, band=0.08, jump_bins=2, smooth=3):
    """Ridge tracing by dynamic programming (Viterbi) inside +-band of the fitted curve: maximise the sum of
    (3-frame smoothed) whitened brightness with at most `jump_bins` frequency bins of movement per frame,
    then keep frames whose smoothed Z >= zmin. Robust for faint curves that drop in and out.
    Returns [[t, f], ...] (the 'traced composite line')."""
    i0 = max(0, np.searchsorted(sp.t, t0 - half_window_s)); i1 = min(sp.t.size, np.searchsorted(sp.t, t0 + half_window_s))
    if i1 - i0 < 8: return []
    Zs = ndimage.uniform_filter1d(sp.z[i0:i1], smooth, axis=0)
    fm = np.array([float(fmodel(t)) for t in sp.t[i0:i1]])
    nb_ = int(np.ceil(fm.max() * band / sp.df)) + 1
    T = i1 - i0; W = 2 * nb_ + 1
    base = np.round((fm - sp.f[0]) / sp.df).astype(int)
    E = np.full((T, W), -5.0)
    for k in range(T):
        jj = base[k] + np.arange(-nb_, nb_ + 1)
        ok = (jj >= 0) & (jj < sp.f.size)
        E[k, ok] = np.clip(Zs[k, jj[ok]], -3, 8)
    D = E.copy(); B = np.zeros((T, W), np.int32)
    for k in range(1, T):
        sh = base[k] - base[k - 1]
        for w in range(W):
            lo = max(0, w + sh - jump_bins); hi = min(W, w + sh + jump_bins + 1)
            if hi <= lo: D[k, w] = -1e9; continue
            j = lo + int(np.argmax(D[k - 1, lo:hi])); D[k, w] += D[k - 1, j]; B[k, w] = j
    path = np.zeros(T, int); path[-1] = int(np.argmax(D[-1]))
    for k in range(T - 1, 0, -1): path[k - 1] = B[k, path[k]]
    pts = []
    for k in range(0, T, 2):
        if E[k, path[k]] >= zmin:
            jj = base[k] + path[k] - nb_
            pts.append([float(sp.t[i0 + k]), float(sp.f[jj])])
    return pts
