"""Harmonic-aware Doppler fitting (optional; NOT part of the video's method, which fits one tone).

A propeller/rotor/engine produces a comb k*F (k = 1, 2, ...). Doppler multiplies every line by the same ratio
g(t) = c/(c+v_r), so a clean fit to ANY single harmonic gives the same v, d, t0 -- harmonics only hurt when the
traced ridge hops from one comb line to another (which fakes a bigger or smaller frequency drop and thus v).

This module:
  * find_comb():      fundamental F and the harmonic number k of the event's line (comb score over all lines),
  * comb_fit():       joint fit of (F, v, d, t0) to every visible harmonic, each traced inside a band narrower
                      than half the line spacing (the ridge cannot change harmonic), plus per-harmonic fits,
  * hop_check():      does a traced line change harmonic number along the pass? (k_est = f / (F g(t))),
  * harmonic_refit(): event dict -> event dict with the joint fit (keeps the event's own line's harmonic as f0).
"""
import math
import numpy as np
from . import C_SOUND
from .physics import retarded_line_g, retarded_line_curve, fit_readouts

CLIP = (-2.0, 6.0)


def _harm_scores(sp, F, v, d, t0, ks, taus):
    """Mean clipped whitened brightness under k*F*g(t) for each k (NaN outside the spectrogram)."""
    g = retarded_line_g(taus, v, d, t0)
    it = np.clip(np.round((taus - sp.t[0]) / sp.dt).astype(int), 0, sp.t.size - 1)
    out = np.full(len(ks), np.nan)
    for n, k in enumerate(ks):
        fq = k * F * g
        y = (fq - sp.f[0]) / sp.df
        ok = (y >= 0) & (y < sp.f.size - 1)
        if ok.sum() < 0.5 * taus.size: continue
        j = y[ok].astype(int); a = y[ok] - j
        z = sp.z[it[ok], j] * (1 - a) + sp.z[it[ok], j + 1] * a
        out[n] = float(np.mean(np.clip(z, *CLIP)))
    return out


def _taus(sp, t0, half_window_s):
    t = np.arange(t0 - half_window_s, t0 + half_window_s, sp.dt)
    return t[(t > sp.t[0]) & (t < sp.t[-1])]


def find_comb(sp, ev, kmax=40, fmin_fund=4.0, fmax=None, half_window_s=45.0, rel_keep=0.75, min_comb=0.5, min_neighbour=0.6):
    """Try F = f0/k for k = 1..kmax. Comb score = mean over k'=1..K(F) of the per-harmonic score (lines up to fmax).
    A sub-harmonic F/2 puts half its lines on noise, so its mean drops ~2x; a super-harmonic 2F skips lines but can tie,
    so we keep the LOWEST F whose comb score is >= rel_keep * best. Returns dict(F, k_line, comb_score, table)."""
    f0, v, d, t0 = ev["f0"], ev["v"], ev["d"], ev["t0"]
    fmax = fmax or min(float(sp.f[-1]), 1450.0)
    fpre = C_SOUND / (C_SOUND - min(v, 300))
    taus = _taus(sp, t0, half_window_s)
    rows = []
    for k in range(1, kmax + 1):
        F = f0 / k
        if F < fmin_fund: break
        K = int(fmax / (F * fpre))
        if K < 2: continue
        ks = np.arange(1, K + 1)
        s = _harm_scores(sp, F, v, d, t0, ks, taus)
        if np.isfinite(s).sum() < 2: continue
        nb = [s[j - 1] for j in (k - 1, k + 1) if 1 <= j <= K and np.isfinite(s[j - 1])]
        rows.append(dict(k_line=k, F=F, comb=float(np.nanmean(s)), n=int(np.isfinite(s).sum()),
                         neighbour=float(max(nb)) if nb else float("nan")))
    if not rows: return None
    best = max(r["comb"] for r in rows)
    # the line's own neighbours (k_line +- 1) must be visible too: a comb of some OTHER source that merely contains
    # the line's frequency as a high harmonic is not this line's family
    ok = [r for r in rows if r["comb"] >= rel_keep * best and r["comb"] > 0 and (r["k_line"] == 1 or r["neighbour"] >= min_neighbour)]
    pick = max(ok, key=lambda r: r["k_line"]) if ok else max(rows, key=lambda r: r["comb"])
    # a comb must stand out from noise: otherwise (no visible harmonic family) keep the line as its own fundamental
    if pick["comb"] < min_comb:
        return None
    return dict(F=pick["F"], k_line=pick["k_line"], comb_score=pick["comb"], table=rows)


def _trace_k(sp, F, k, v, d, t0, half_window_s, zmin):
    """Trace harmonic k inside a band of +-0.4 line spacings (never wider than the default 8 %)."""
    from .audio import _trace
    band = min(0.08, 0.4 / k)
    return _trace(sp, lambda t: k * F * retarded_line_g(t, v, d, t0), t0, half_window_s, 0.03, zmin, band=band,
                  jump_bins=max(1, min(2, int(round(k * F * band / sp.df / 3)))))


def _lsq(L, ks, x0, t0w=20.0):
    """Joint retarded fit; residual in % of the line's own frequency so every harmonic weighs alike."""
    from scipy.optimize import least_squares
    T, Fo, K = L[:, 0], L[:, 1], ks
    fun = lambda q: 100.0 * (K * q[0] * retarded_line_g(T, q[1], q[2], q[3]) - Fo) / Fo
    r = least_squares(fun, x0, bounds=([1, 5, 30, x0[3] - t0w], [5000, 320, 40000, x0[3] + t0w]), loss="soft_l1", f_scale=0.5)
    J = r.jac; cov = np.linalg.pinv(J.T @ J) * max(1e-9, np.mean(r.fun ** 2))
    return r, np.sqrt(np.clip(np.diag(cov), 0, None))


def comb_fit(sp, ev, kmax=40, half_window_s=45.0, zmin=1.5, min_score=0.6, min_pts=15, fmax=None, comb=None, min_harmonics=3):
    """Joint (F, v, d, t0) fit over every visible harmonic of the event's comb.
    Returns dict(F, k_line, v, d, t0, t0_sd, rms_pct, rms_hz_line, harmonics=[{k, n, score, v, d, t0, rms_hz}], lines={k: line})."""
    comb = comb or find_comb(sp, ev, kmax=kmax, fmax=fmax, half_window_s=half_window_s)
    if comb is None: return None
    F, kl = comb["F"], comb["k_line"]
    v, d, t0 = ev["v"], ev["d"], ev["t0"]
    fmax = fmax or min(float(sp.f[-1]), 1450.0)
    K = int(fmax / (F * C_SOUND / (C_SOUND - min(v, 300))))
    ks = np.arange(1, K + 1)
    s = _harm_scores(sp, F, v, d, t0, ks, _taus(sp, t0, half_window_s))
    vis = [int(k) for k, x in zip(ks, s) if np.isfinite(x) and x >= min_score]
    if kl not in vis: vis.append(kl)
    from scipy.optimize import least_squares
    per, rowsL, rowsK, lines = [], [], [], {}
    for k in sorted(vis):
        L = np.asarray(_trace_k(sp, F, k, v, d, t0, half_window_s, zmin), float)
        if len(L) < min_pts: continue
        lines[k] = L.tolist()
        fun = lambda q: retarded_line_curve(L[:, 0], q[0], q[1], q[2], q[3]) - L[:, 1]
        try:
            r = least_squares(fun, [k * F, v, d, t0], bounds=([1, 5, 30, t0 - 20], [5000, 320, 40000, t0 + 20]), loss="soft_l1", f_scale=1.0)
            per.append(dict(k=k, n=len(L), score=float(s[k - 1]), f0=float(r.x[0]), F_k=float(r.x[0] / k), v=float(r.x[1]),
                            d=float(r.x[2]), t0=float(r.x[3]), rms_hz=float(np.sqrt(np.mean(r.fun ** 2)))))
        except Exception:
            continue
        rowsL.append(L); rowsK.append(np.full(len(L), k))
    if len(rowsL) < min_harmonics: return None      # one line only: nothing to gain over the single-line fit
    L = np.concatenate(rowsL); KK = np.concatenate(rowsK).astype(float)
    r, sd = _lsq(L, KK, [F, v, d, t0])
    # one robust pass: drop harmonics whose own residual is >3x the median (cross-talk from another source)
    res = np.abs(r.fun); med = np.median([np.median(res[KK == k]) for k in np.unique(KK)])
    bad = [int(k) for k in np.unique(KK) if np.median(res[KK == k]) > 3 * max(med, 0.2)]
    if bad and len(bad) < len(np.unique(KK)):
        m = ~np.isin(KK, bad); r, sd = _lsq(L[m], KK[m], r.x); L, KK = L[m], KK[m]
    Fj, vj, dj, tj = (float(x) for x in r.x)
    rms_line = float(np.sqrt(np.mean((kl * Fj * retarded_line_g(L[KK == kl, 0], vj, dj, tj) - L[KK == kl, 1]) ** 2))) if (KK == kl).any() else None
    return dict(F=Fj, k_line=kl, f0=kl * Fj, v=vj, d=dj, t0=tj, t0_sd=float(sd[3]), v_sd=float(sd[1]), d_sd=float(sd[2]),
                rms_pct=float(np.sqrt(np.mean(r.fun ** 2))), rms_hz_line=rms_line, used=[int(k) for k in np.unique(KK)],
                dropped=bad, harmonics=per, comb_score=comb["comb_score"], lines=lines)


def hop_check(line, F, v, d, t0):
    """Harmonic number along a traced line: k_est = f / (F g(t)). A hop shows up as more than one rounded k."""
    L = np.asarray(line, float)
    if L.size == 0: return dict(ks=[], hop=False)
    k_est = L[:, 1] / (F * retarded_line_g(L[:, 0], v, d, t0))
    kr = np.round(k_est).astype(int)
    pre = kr[L[:, 0] < t0]; post = kr[L[:, 0] >= t0]
    mode = lambda a: int(np.bincount(a).argmax()) if a.size else None
    return dict(k_pre=mode(pre), k_post=mode(post), ks=sorted(set(kr.tolist())), hop=len(set(kr.tolist())) > 1,
                k_est_min=float(k_est.min()), k_est_max=float(k_est.max()))


def asymptote_ratio(line, t0, edge_s=25.0):
    """Median f before (t < t0-edge) / after (t > t0+edge), and the nearest small-integer ratio n/(n-1)."""
    L = np.asarray(line, float)
    pre = L[L[:, 0] < t0 - edge_s, 1]; post = L[L[:, 0] > t0 + edge_s, 1]
    if not (pre.size and post.size): return None
    r = float(np.median(pre) / np.median(post))
    n = max(2, int(round(r / (r - 1)))) if r > 1 else None
    return dict(ratio=r, v_from_ratio=C_SOUND * (r - 1) / (r + 1), nearest_n_over_n_minus_1=(f"{n}/{n-1}" if n else None),
                nearest_value=(n / (n - 1) if n else None))


def harmonic_refit(sp, ev, **kw):
    """Event -> copy with (f0, v, d, t0) from the joint harmonic fit (f0 stays on the event's own harmonic, so the
    single-flight line score and the multi-event f0 sweeps are unchanged in meaning). The original values are kept
    under 'single_line_fit'; the v/d ranges are scaled by the change in v/d and t0_sigma never shrinks."""
    from .audio import V_REL, D_LO, D_HI
    cf = comb_fit(sp, ev, **kw)
    if cf is None: return dict(ev)
    out = dict(ev)
    out["single_line_fit"] = {k: ev[k] for k in ("f0", "v", "d", "t0") if k in ev}
    # ranges: the event's own (auto-generous or the user's manual) ranges, scaled by the change in v and d
    rv, rd = cf["v"] / ev["v"], cf["d"] / ev["d"]
    out.update(f0=cf["f0"], v=cf["v"], d=cf["d"], t0=cf["t0"], t0_sigma=float(max(0.25, cf["t0_sd"], ev.get("t0_sigma", 0.25))),
               v_min=ev.get("v_min", ev["v"] * (1 - V_REL)) * rv, v_max=ev.get("v_max", ev["v"] * (1 + V_REL)) * rv,
               d_min=ev.get("d_min", ev["d"] * D_LO) * rd, d_max=ev.get("d_max", ev["d"] * D_HI) * rd, model="retarded_comb")
    if cf["k_line"] in cf["lines"]: out["line"] = cf["lines"][cf["k_line"]]
    out["v_best"] = 0.5 * (out["v_min"] + out["v_max"]); out["d_best"] = 0.5 * (out["d_min"] + out["d_max"])
    out.update(fit_readouts(out["f0"], out["v"], out["d"]))
    out["comb"] = {k: cf[k] for k in cf if k != "lines"}
    return out
