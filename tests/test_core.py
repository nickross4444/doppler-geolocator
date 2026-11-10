"""Unit tests that pin the video's formulas and the author's on-screen algorithms (SPEC §3.2, §4.3, §4.4, §5.1)."""
import math, re, os, glob
import numpy as np
import pytest
from conftest import APP
from dopp.physics import worked_example, fit_readouts, straight_line_curve, retarded_line_g
from dopp import project as P
from dopp.search import _nn_batch, cheap_silence_check, finalize_ranks, crude_speed_distance_score, Params
import polars as pl

def test_worked_example_96_07_hz():
    """On-screen step 11/11: t_r 25.00 s -> t_e 16.910 s, r 2775 m, v_r 121.16 m/s, f = 96.07 Hz."""
    w = worked_example()
    assert abs(w["t_e"] - 16.910) < 0.005
    assert abs(w["r"] - 2775) < 1.0
    assert abs(w["v_r"] - 121.16) < 0.05
    assert round(w["f"], 2) == 96.07

def test_slider_readouts_match_screen():
    """Doppler Curve Fit tool: f0 131.5, v 106.5, d 3130 -> f_pre 190.72, f_post 100.34, max RoC 1.389."""
    r = fit_readouts(131.5, 106.5, 3130)
    assert round(r["f_pre"], 2) == 190.72 and round(r["f_post"], 2) == 100.34 and round(r["max_roc"], 3) == 1.389

def test_position_vs_delayed_are_different_curves():
    """Video 16:40: the propagation-delay curve is a different (asymmetric) curve, not a time shift."""
    t = np.linspace(-60, 60, 241)
    fpos = straight_line_curve(t, 130, 140, 980, 0.0)
    fdel = 130 * retarded_line_g(t, 140, 980, 0.0)
    best = min(np.nanmax(np.abs(np.interp(t, t + s, fpos) - fdel)) for s in np.arange(-10, 10, 0.05))
    assert best > 1.0          # no time shift makes them equal

def test_gap_is_derived_from_fitted_t0s_all_pairs():
    ev = [dict(id=1, t0=100.0, t0_sigma=0.5), dict(id=2, t0=1100.5, t0_sigma=0.3), dict(id=3, t0=400.25, t0_sigma=1.0)]
    g = P.gap_table(ev)
    assert len(g) == 3                                  # N events -> N(N-1)/2 pairs
    gaps = sorted(round(r["gap_s"], 2) for r in g)
    assert gaps == [300.25, 700.25, 1000.5]
    p = Params()
    r = [x for x in g if x["a"] == 1 and x["b"] == 2][0]
    assert abs(r["tol_s"] - (p.offset_tol_s + p.tol_sigma_k * math.hypot(0.5, 0.3))) < 1e-9

def test_no_hardcoded_920():
    for f in glob.glob(os.path.join(APP, "dopp", "*.py")) + [os.path.join(APP, "server.py")]:
        import io, tokenize
        toks = tokenize.generate_tokens(io.StringIO(open(f).read()).readline)
        src = " ".join(t.string for t in toks if t.type == tokenize.NUMBER)   # numeric literals only (not docs)
        assert not re.search(r"(?<![\d.])92[0-3](\.\d+)?(?![\d.])", src), f"literal gap in {f}"

def _ref_best_combo(times1, times2, gap):
    """Line-by-line port of the on-screen best_combo_batch_nearest_neighbor for one row (later = earlier + gap)."""
    m = len(times2); s = np.argsort(times2); t2 = np.asarray(times2)[s]; t1 = np.asarray(times1)
    targets = t1 + gap
    ins = np.searchsorted(t2, targets)
    lo = np.clip(ins - 1, 0, m - 1); hi = np.clip(ins, 0, m - 1)
    dlo = (t2[lo] - t1) - gap; dhi = (t2[hi] - t1) - gap
    use_lo = np.abs(dlo) <= np.abs(dhi)
    best = np.where(use_lo, dlo, dhi); j = np.where(use_lo, lo, hi)
    i = int(np.argmin(np.abs(best)))
    return i, int(s[j[i]]), float(best[i])

def test_offset_filter_matches_author_algorithm():
    rng = np.random.default_rng(1)
    for _ in range(200):
        n1, n2 = rng.integers(1, 8, 2)
        t1 = rng.uniform(0, 5000, n1); t2 = rng.uniform(0, 6000, n2); gap = rng.uniform(100, 2000)
        bi, bj, bd = _nn_batch(np.array([0, n1]), t1, np.array([0, n2]), t2, gap)
        i, j, d = _ref_best_combo(t1, t2, gap)
        assert abs(abs(bd[0]) - abs(d)) < 1e-9

def test_silence_rule_max_cpa_no_multiplier():
    """Slide: disqualify a point that heard >= 1 other non-jet flight between the events, threshold = max(CPA1, CPA2)."""
    p = Params()
    base = dict(cpa_time_utc_1=1000.0, cpa_time_utc_2=1920.0, cpa_distance_m_1=3000.0, cpa_distance_m_2=5000.0,
                flightid_1=1, flightid_2=2)
    row = dict(base, all_flightids_1=[1, 7], all_cpa_times_1=[1000.0, 1500.0], all_cpa_distances_1=[3000.0, 4900.0],
               all_flightids_2=[2], all_cpa_times_2=[1920.0], all_cpa_distances_2=[5000.0])
    assert cheap_silence_check(row, 2, {1, 2, 7}, p)[0] is True          # 4.9 km < 5 km -> excluded
    row["all_cpa_distances_1"] = [3000.0, 5100.0]
    assert cheap_silence_check(row, 2, {1, 2, 7}, p)[0] is False         # 5.1 km > max CPA -> kept
    row["all_cpa_distances_1"] = [3000.0, 100.0]
    assert cheap_silence_check(row, 2, {1, 2}, p)[0] is False            # a jet does not count
    row["all_cpa_times_1"] = [1000.0, 2500.0]
    assert cheap_silence_check(row, 2, {1, 2, 7}, p)[0] is False         # outside the window

def test_crude_score_and_author_rank_sum():
    assert crude_speed_distance_score(110, 3000, 100, 2500) == pytest.approx(-(0.1 + 0.2))
    df = pl.DataFrame(dict(match_score=[-0.1, -0.5, -0.2], silence_penalty=[0.0, -3000.0, -100.0]))
    a = finalize_ranks(df, "author")["combined_rank"].to_list()
    c = finalize_ranks(df, "corrected")["combined_rank"].to_list()
    assert sorted(a) == [1, 2, 3] and sorted(c) == [1, 2, 3]
    assert c[0] == 1      # corrected: best match AND quietest wins

def test_silence_windows_n3_consecutive_vs_span():
    from dopp.search import silence_windows
    p = Params()
    row = {"flightid_1": 1, "flightid_2": 2, "flightid_3": 3,
           "cpa_time_utc_1": 0.0, "cpa_time_utc_2": 1000.0, "cpa_time_utc_3": 2000.0,
           "cpa_distance_m_1": 8000.0, "cpa_distance_m_2": 2000.0, "cpa_distance_m_3": 2000.0}
    assert silence_windows(row, 3, p) == [(0.0, 1000.0, 8000.0), (1000.0, 2000.0, 2000.0)]
    for k in (1, 2, 3):
        row[f"all_flightids_{k}"] = [7]; row[f"all_cpa_times_{k}"] = [1500.0]; row[f"all_cpa_distances_{k}"] = [5000.0]
    assert cheap_silence_check(row, 3, None, p)[0] is False      # 5 km at t=1500 is farther than the 2 km pair -> kept
    p.silence_windows = "span"
    assert silence_windows(row, 3, p) == [(0.0, 2000.0, 8000.0)]
    assert cheap_silence_check(row, 3, None, p)[0] is True       # one 0..2000 s window with an 8 km threshold -> excluded

def test_wind_models():
    """'full' moving-medium model: a steady wind cancels to first order for a stationary listener;
    'air_los': the source's air-relative speed sets the shift (headwind -> larger apparent speed)."""
    import numpy as np
    from dopp.physics import model_g_many
    t = np.arange(0.0, 400.0); v = 63.0
    lat0, lon0 = 49.0, 12.0
    y = (t - 200.0) * v                                   # northbound, CPA 1 km east of the listener at t=200
    tr = dict(t=t, lat=lat0 + y / 111320.0, lon=np.full(t.size, lon0 + 1000.0 / (111320.0 * np.cos(np.radians(lat0)))),
              alt_m=np.full(t.size, 300.0), gs=np.full(t.size, v), trk=np.zeros(t.size), vs=np.zeros(t.size), valid=np.ones(t.size, bool))
    T = np.array([[60.0, 390.0]])
    g0 = model_g_many(tr, [lat0], [lon0], [300.0], T)[0]
    gf = model_g_many(tr, [lat0], [lon0], [300.0], T, wind=(0.0, -14.0), wind_model="full")[0]
    ga = model_g_many(tr, [lat0], [lon0], [300.0], T, wind=(0.0, -14.0), wind_model="air_los")[0]
    ratio = lambda g: g[0] / g[1]
    vapp = lambda R: 343.0 * (R - 1) / (R + 1)
    assert abs(vapp(ratio(g0)) - vapp(ratio(gf))) < 1.5          # first-order cancellation
    assert vapp(ratio(ga)) - vapp(ratio(g0)) > 10                 # ~ +14 m/s apparent speed
