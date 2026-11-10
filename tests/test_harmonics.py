"""Harmonic-aware fitting (dopp/harmonics.py; optional, default off)."""
import os
import numpy as np
import pytest
from conftest import APP
from dopp.audio import Spectrogram
from dopp.physics import retarded_line_g
from dopp.harmonics import find_comb, comb_fit, hop_check, asymptote_ratio, harmonic_refit

F0, V, D, T0 = 17.0, 70.0, 1200.0, 100.0

def _synthetic(ks=range(3, 30), seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(0, 200, 0.25); f = np.arange(20.0, 1500.0, 1.0)
    z = rng.normal(0, 1, (t.size, f.size)).astype(np.float32)
    g = retarded_line_g(t, V, D, T0)
    for k in ks:
        fk = k * F0 * g; j = np.round(fk - f[0]).astype(int); ok = (j >= 0) & (j < f.size)
        for dj, a in ((-1, 2.5), (0, 5.0), (1, 2.5)):          # Hann-like 3-bin line
            jj = np.clip(j + dj, 0, f.size - 1); z[np.flatnonzero(ok), jj[ok]] += a
    return Spectrogram(t, f, z, z, {})

def _event(f0=6 * F0 * 1.003, v=V * 1.02, d=D * 1.05, t0=T0 + 0.3):   # a slightly-off single-line fit
    return dict(f0=f0, v=v, d=d, t0=t0, v_min=v * 0.88, v_max=v * 1.12, d_min=d * 0.6, d_max=d * 1.45, t0_sigma=0.5, line=[])

def test_comb_found_and_joint_fit_recovers_v():
    sp = _synthetic(); e = _event()
    c = find_comb(sp, e)
    assert c is not None and c["k_line"] == 6 and abs(c["F"] - e["f0"] / 6) < 1e-9
    cf = comb_fit(sp, e, comb=c)
    assert abs(cf["F"] - F0) < 0.05 and abs(cf["v"] - V) < 1.0 and abs(cf["t0"] - T0) < 0.3
    assert len(cf["used"]) >= 20

def test_no_comb_on_a_single_tone():
    sp = _synthetic(ks=[6]); assert harmonic_refit(sp, _event()).get("comb") is None

def test_refit_keeps_line_harmonic_and_scales_ranges():
    sp = _synthetic(); e = _event(); r = harmonic_refit(sp, e)
    assert r["comb"]["k_line"] == 6 and abs(r["f0"] - 6 * F0) < 0.3
    assert abs(r["v_min"] / r["v"] - e["v_min"] / e["v"]) < 1e-9 and r["single_line_fit"]["v"] == e["v"]

def test_hop_check_flags_a_harmonic_hop_and_the_fake_speed():
    t = np.arange(60, 140, 0.5); g = retarded_line_g(t, V, D, T0)
    k = np.where(t < T0, 7, 6)                                   # higher harmonic before CPA, lower after
    line = np.c_[t, k * F0 * g]
    h = hop_check(line, F0, V, D, T0)
    assert h["hop"] and h["k_pre"] == 7 and h["k_post"] == 6
    a = asymptote_ratio(line, T0)
    assert a["v_from_ratio"] > V + 15                           # the hop fakes a much larger drop

@pytest.mark.skipif(not os.path.exists(os.path.join(APP, "data", "projects", "altenthann", "spec.npz")), reason="preset missing")
def test_altenthann_no_harmonic_hop_and_comb_v():
    """Nicholas's question: the Altenthann fit sits on the 6th harmonic of a 17.13 Hz comb, never hops, and every
    harmonic (42 lines jointly) gives ~78 m/s, so harmonics are not why D-HAIB (63 m/s) does not match."""
    from dopp import project as P
    sp = P.spec("altenthann"); e = [x for x in P.events("altenthann") if x.get("selected")][0]
    cf = comb_fit(sp, e)
    assert cf["k_line"] == 6 and abs(cf["F"] - 17.13) < 0.05 and len(cf["used"]) >= 20
    assert 76 < cf["v"] < 80
    assert not hop_check(e["line"], cf["F"], cf["v"], cf["d"], cf["t0"])["hop"]
    assert asymptote_ratio(e["line"], e["t0"])["v_from_ratio"] > 68     # model-free lower bound > 63 m/s
