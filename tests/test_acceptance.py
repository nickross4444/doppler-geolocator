"""Acceptance tests on the preloaded examples. By default they check the cached preset results written by
scripts/build_presets.py; DOPP_LIVE=1 re-runs the regional searches through the library (same code as the server)."""
import os, json, math
import pytest
from conftest import APP, LIVE, preset_results
from dopp import project as P

def _get(pid, tag):
    if LIVE and tag.endswith("region") or (LIVE and tag == "single_region"):
        return _live(pid, tag)
    r = preset_results(pid, tag=tag)
    if not r: pytest.skip(f"no cached preset result {pid}/{tag} (run scripts/build_presets.py)")
    return r[0]

def _live(pid, tag):
    """Re-run the stored job definition (events + params) directly."""
    from dopp.adsb import AdsbLolProvider
    from dopp.search import Params, search_multi, search_single
    from dopp import results as R
    j = preset_results(pid, tag=tag)[0]
    st = AdsbLolProvider().load_day(j["day"]); sp = P.spec(pid); m = P.meta(pid)
    fields = Params.__dataclass_fields__
    p = Params(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in j["params"].items() if k in fields})
    if j["mode"] == "multi": res = search_multi(st, sp, j["events"], p, log=lambda s: None); N = len(j["events"])
    else:
        e = j["events"][0]; res = search_single(st, sp, e, e["line"], j["rec_start"], p, log=lambda s: None); N = 1
        res["mode"] = "single"
    res.setdefault("mode", j["mode"])
    return dict(j, summary=R.summarize(res, st, N, tuple(m["truth"][:2])))

def _top(j): return j["summary"]["combos"]

# ---------------------------------------------------------------- Oyster Bay (the video's own example)
def test_oyster_gap_derived_from_fits():
    j = _get("oysterbay", "multi_region")
    ev = sorted(j["events"], key=lambda e: e["t0"])
    gap = ev[1]["t0"] - ev[0]["t0"]
    assert 915 < gap < 926          # ~920 s arises from the fitted t0s (author on screen: 1:35:52 -> 1:51:13-14.8)

@pytest.mark.parametrize("tag", ["multi_region", "multi_region_auto"])
def test_oyster_truth_rank1_regional(tag):
    j = _get("oysterbay", tag); c = _top(j)
    assert c[0]["dist_to_truth_m"] < 300, c[0]
    regs = {f["reg"] for f in c[0]["flights"]}
    assert regs == {"N22NY", "N93CQ"}       # PC12 + B350, the author's flights 1100069709 + 1100048665
    print(f"\n{tag}: #1 {c[0]['total_score']:.3f} @ {c[0]['dist_to_truth_m']:.0f} m, #2 {c[1]['total_score']:.3f} "
          f"(margin {100 * (c[0]['total_score'] - c[1]['total_score']) / c[1]['total_score']:.1f} %)")

def test_oyster_truth_conus():
    j = _get("oysterbay", "multi_conus"); c = _top(j)
    m = 100 * (c[0]["total_score"] - c[1]["total_score"]) / c[1]["total_score"]
    print(f"\nCONUS: #1 {c[0]['total_score']:.3f} {[f['reg'] for f in c[0]['flights']]} @ {c[0]['dist_to_truth_m']:.0f} m; "
          f"#2 {c[1]['total_score']:.3f} {[f['reg'] for f in c[1]['flights']]} @ {c[1]['dist_to_truth_m']/1000:.0f} km; margin {m:.1f} %")
    assert j["summary"]["truth_rank"] is not None and j["summary"]["truth_rank"] <= 3
    if c[0]["dist_to_truth_m"] < 300: assert m > 0

def test_oyster_single_flight_mode():
    """Faint jet at ~21:16 (author: flight 1100029523, -1.74, dt +0.45 s), single-flight known-time mode."""
    j = _get("oysterbay", "single_region"); c = _top(j)
    assert c[0]["flights"][0]["reg"] == "N325PQ"
    assert c[0]["dist_to_truth_m"] < 400
    assert "histogram" in j["summary"]
    print(f"\nsingle: #1 {c[0]['total_score']:.3f} @ {c[0]['dist_to_truth_m']:.0f} m dt {c[0]['dt_s']:+.1f} s; #2 {c[1]['total_score']:.3f}")

def test_oyster_auto_detects_both_events():
    ev = [e for e in P.events("oysterbay") if e.get("source") == "auto"]
    for t in (2155.0, 3076.0):
        hit = [e for e in ev if abs(e["t0"] - t) < 4]
        assert hit, t
        assert max(e["conf"] for e in hit) > 6

# ---------------------------------------------------------------- Livermore (Zenodo 10.5281/zenodo.23424278)
LV = ["livermore_n394fx_r20", "livermore_n4040_r12", "livermore_n413ak_r49", "livermore_n788bm_r03", "livermore_n916kt_r22"]

@pytest.mark.parametrize("pid", LV)
def test_livermore_truth_in_top_matches_regional(pid):
    j = _get(pid, "single_region"); c = _top(j)
    reg = pid.split("_")[1].upper()
    rk = next((r["rank"] for r in c if r["flights"][0]["reg"] == reg and r["dist_to_truth_m"] < 1000), None)
    print(f"\n{pid}: truth flight {reg} rank {rk}; #1 {c[0]['flights'][0]['reg']} {c[0]['total_score']:.3f} @ {c[0]['dist_to_truth_m']:.0f} m")
    assert rk is not None and rk <= 5

def test_livermore_conus_report():
    """US-wide single-flight is near-degenerate (reported, not asserted per case): at least one case recovers truth #1."""
    ranks = {}
    for pid in LV:
        r = preset_results(pid, tag="single_conus")
        if r: ranks[pid] = r[0]["summary"].get("truth_rank")
    if not ranks: pytest.skip("no CONUS Livermore results")
    print("\nLivermore CONUS truth ranks:", ranks)
    assert any(v == 1 for v in ranks.values())

# ---------------------------------------------------------------- extra outdoor examples
def test_altenthann_truth_flight_in_top_matches():
    j = _get("altenthann", "single_region"); c = _top(j)
    rk = next((r["rank"] for r in c if r["flights"][0]["reg"] == "D-HAIB"), None)
    print(f"\naltenthann: D-HAIB rank {rk}; #1 {c[0]['flights'][0]['reg'] or c[0]['flights'][0]['hex']} {c[0]['total_score']:.3f}"
          f" @ {c[0]['dist_to_truth_m']/1000:.1f} km")
    assert rk is not None and rk <= 10

# ---------------------------------------------------------------- Cornell FeederWatch live cam (video, faint turboprop)
def test_feederwatch_auto_detects_king_air():
    ev = [e for e in P.events("ithaca_feederwatch") if e.get("source") == "auto"]
    hit = [e for e in ev if 1880 <= e["t0"] <= 1925 and 100 <= e["f0"] <= 130]
    assert hit and max(e["conf"] for e in hit) > 10      # among the strongest detections of the 42 min clip

@pytest.mark.parametrize("tag", ["single_region", "single_region_wind"])
def test_feederwatch_single_flight(tag):
    j = _get("ithaca_feederwatch", tag); c = _top(j)
    assert c[0]["flights"][0]["reg"] == "95-00088"       # US Army BE20, 7.2 km slant
    assert c[0]["dist_to_truth_m"] < 2500
    print(f"\nfeederwatch {tag}: #1 {c[0]['flights'][0]['reg']} {c[0]['total_score']:.3f} @ {c[0]['dist_to_truth_m']:.0f} m dt {c[0]['dt_s']:+.1f} s")

# ---------------------------------------------------------------- Venice Beach live cam (video, faint GA passes, 9 Nov 2026)
VB_PASSES = {"N882AB": (320, 330, 45, 70), "N350TV": (870, 880, 45, 70), "N56114": (2310, 2318, 45, 70), "N171CP": (2570, 2580, 35, 50)}

def test_venice_auto_detects_ga_passes():
    """Each heard GA pass (ADS-B heard-CPA within ~5 s) has an auto event whose fitted speed matches the aircraft."""
    ev = [e for e in P.events("venice_beach") if e.get("source", "auto") == "auto"]
    for reg, (t0, t1, v0, v1) in VB_PASSES.items():
        hit = [e for e in ev if t0 <= e["t0"] <= t1 and v0 <= e["v"] <= v1 and e["d"] < 1500]
        assert hit, reg

def test_venice_multi_region():
    """Two faint passes (SR20 + PA46, 549 s apart): the multi-event search lands on the hotel."""
    j = _get("venice_beach", "multi_region"); c = _top(j)
    assert {f["reg"] for f in c[0]["flights"]} == {"N882AB", "N350TV"}
    assert c[0]["dist_to_truth_m"] < 300
    m = (c[0]["total_score"] - c[1]["total_score"]) / c[1]["total_score"]
    print(f"\nvenice multi: #1 {c[0]['total_score']:.3f} @ {c[0]['dist_to_truth_m']:.0f} m, #2 {c[1]['total_score']:.3f} (margin {100*m:.0f} %)")
    assert m > 0.1

def test_venice_multi_region_b_report():
    """Second pair (P28R + C172, 261 s apart): right flights in the top 3 but ~2 km off (reported)."""
    j = _get("venice_beach", "multi_region_b"); c = _top(j)
    rk = next((r["rank"] for r in c if {f["reg"] for f in r["flights"]} == {"N56114", "N171CP"}), None)
    print(f"\nvenice multi_b: true pair rank {rk}")
    assert rk is not None and rk <= 5

@pytest.mark.parametrize("tag,reg,max_rank,max_m", [("single_region", "N350TV", 1, 500), ("single_region_c172", "N171CP", 1, 700),
                                                      ("single_region_sr20", "N882AB", 5, 1000), ("single_region_p28r", "N56114", 5, 1000)])
def test_venice_single_flight(tag, reg, max_rank, max_m):
    j = _get("venice_beach", tag); c = _top(j)
    rk = next((r["rank"] for r in c if r["flights"][0]["reg"] == reg and r["dist_to_truth_m"] < max_m), None)
    print(f"\nvenice {tag}: {reg} rank {rk}; #1 {c[0]['flights'][0]['reg']} {c[0]['total_score']:.3f} @ {c[0]['dist_to_truth_m']:.0f} m")
    assert rk is not None and rk <= max_rank
