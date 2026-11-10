"""Export the cached preset data to a static, read-only viewer (no Python backend).

usage:  ../.venv/bin/python scripts/export_static.py [--out ../viewer/site] [--only oysterbay,altenthann]

Writes <out>/ = web_static/* (viewer HTML/JS/CSS) + data/:
  data/index.json                      preset list
  data/<pid>/project.json              meta, events (fitted params + traced ridges), gap table, spectrogram tile index,
                                       audio snippet index, result list
  data/<pid>/spec_ov.jpg               whole-recording overview (max-pooled in time)
  data/<pid>/spec_<k>.jpg              native-resolution tiles (0.25 s x 1 Hz per pixel, TILE_S seconds each)
  data/<pid>/audio_<k>.mp3             short mono snippets around the events used by the preset results
  data/<pid>/res_<jid>.json            one bundle per cached search: summary/combos/points, events, params, log,
                                       predicted curves and heat maps for every rank that has them cached
Only projects whose meta.json has "preset": true are exported. Nothing in data/projects is modified.
"""
import os, sys, json, glob, shutil, argparse, subprocess, math, time, re
import numpy as np
from PIL import Image

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
from dopp import project as P          # noqa: E402

TILE_S = 600.0            # seconds per native-resolution tile
OV_W = 2400               # overview width (px)
FMAX_IMG = 1500.0
Z_LO, Z_HI = -1.0, 6.0    # same default colour range as the app (z max 6)
JPEG_Q = 82
SNIP_HALF_S = 35.0        # audio snippet = event t0 +- this
SNIP_KBPS = 48
FULL_AUDIO_MAX_S = 130.0  # shorter recordings are exported whole

# viewer-only labels (not in the app's meta.json)
STATUS = {
    "altenthann": dict(status="unsolved", status_note=(
        "The fitted line is the 6th harmonic of a 17.13 Hz rotor comb. It stays on that one harmonic, and all 42 visible "
        "harmonics fitted together give 78 m/s (each clean line alone gives 77-78 m/s), so harmonics are not the problem. "
        "The R44 helicopter near the geotag (D-HAIB) does 63 m/s, which can't produce the measured before/after pitch ratio "
        "(1.52 against at most 1.45), and its rotor would make a 13.6 Hz comb, not 17.1 Hz. A 4-blade rotor at about "
        "256 rpm fits 17.1 Hz, as on an NH90 military helicopter, which doesn't broadcast ADS-B. D-HAIB ranks #5, 1.7 km off. "
        "Kept as an honest negative example: the sound most likely came from an untracked aircraft.")),
}
GROUP = lambda pid: "Livermore CA (audio, exact ground truth)" if pid.startswith("livermore") else None

_ANCH = np.array([[0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122], [229, 80, 100],
                  [251, 135, 97], [254, 194, 135], [252, 253, 191]], float)
LUT = np.stack([np.interp(np.linspace(0, 1, 256), np.linspace(0, 1, len(_ANCH)), _ANCH[:, c]) for c in range(3)], 1).astype(np.uint8)


def rnd(o, nd=4):
    """Round floats recursively (keeps JSON small). Lat/lon need ~6 decimals -> handled by key."""
    if isinstance(o, float):
        if not math.isfinite(o): return None
        return round(o, nd)
    if isinstance(o, dict):
        return {k: rnd(v, 6 if k in ("lat", "lon", "truth") else (2 if k in ("cpa_time_utc",) else nd)) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [rnd(v, nd) for v in o]
    return o


def render(A):
    U = np.clip((A - Z_LO) / (Z_HI - Z_LO), 0, 1)
    return Image.fromarray(LUT[(U.T[::-1] * 255).astype(np.uint8)])


def export_spec(pid, out):
    sp = P.spec(pid)
    jmax = int(np.searchsorted(sp.f, FMAX_IMG, side="right"))
    Z = sp.z[:, :jmax].astype(np.float32); f0 = float(sp.f[0]); f1 = float(sp.f[jmax - 1] + sp.df)
    imgs = []
    # overview (max-pool in time keeps faint ridges)
    k = max(1, int(math.ceil(Z.shape[0] / OV_W)))
    n = (Z.shape[0] // k) * k
    A = Z[:n].reshape(-1, k, Z.shape[1]).max(1) if k > 1 else Z
    im = render(A); im = im.resize((im.width, min(im.height, 740)), Image.BILINEAR)
    im.save(os.path.join(out, "spec_ov.jpg"), quality=JPEG_Q)
    t_a = float(sp.t[0] - sp.dt / 2)
    imgs.append(dict(file="spec_ov.jpg", t0=t_a, t1=t_a + n * sp.dt, f0=f0 - sp.df / 2, f1=f1 - sp.df / 2, level="overview"))
    # native tiles
    per = int(round(TILE_S / sp.dt)); i = 0; kk = 0
    while i < Z.shape[0]:
        j = min(Z.shape[0], i + per)
        if j - i < 2: break
        fn = f"spec_{kk}.jpg"; render(Z[i:j]).save(os.path.join(out, fn), quality=JPEG_Q)
        imgs.append(dict(file=fn, t0=float(sp.t[i] - sp.dt / 2), t1=float(sp.t[j - 1] + sp.dt / 2), f0=f0 - sp.df / 2,
                         f1=f1 - sp.df / 2, level="native"))
        i = j; kk += 1
    return imgs, dict(t0=float(sp.t[0]), t1=float(sp.t[-1]), f0=float(sp.f[0]), f1=float(sp.f[-1]), dt=sp.dt, df=sp.df)


def export_audio(pid, out, meta, event_t0s):
    src = os.path.join(P.pdir(pid), "audio.wav")
    if not os.path.exists(src): src = os.path.join(P.pdir(pid), "audio.mp3")
    dur = float(meta.get("duration_s") or 0); clips = []
    if dur and dur <= FULL_AUDIO_MAX_S: wins = [(0.0, dur, None)]
    else:
        wins = []
        for eid, t0 in sorted(event_t0s.items(), key=lambda x: x[1]):
            a, b = max(0.0, t0 - SNIP_HALF_S), min(dur or t0 + SNIP_HALF_S, t0 + SNIP_HALF_S)
            if wins and a <= wins[-1][1]:     # merge overlapping windows
                wins[-1] = (wins[-1][0], max(b, wins[-1][1]), wins[-1][2] + [eid]); continue
            wins.append((a, b, [eid]))
    for k, (a, b, eids) in enumerate(wins):
        fn = f"audio_{k}.mp3"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src, "-ac", "1",
                        "-b:a", f"{SNIP_KBPS}k", os.path.join(out, fn)], check=True)
        clips.append(dict(file=fn, t0=round(a, 3), t1=round(b, 3), events=eids))
    return clips


def slim_event(e):
    keep = {k: v for k, v in e.items() if k not in ("line",)}
    if e.get("line"): keep["line"] = [[round(t, 2), round(f, 1)] for t, f in e["line"]]
    return rnd(keep)


def export_results(pid, out):
    d = os.path.join(P.pdir(pid), "results"); rows = []
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        jid = os.path.basename(f)[:-5]
        if "_" in jid: continue
        j = P.load_json(f)
        if not j or j.get("status") != "done": continue
        s = j.get("summary") or {}
        b = dict(id=jid, mode=j["mode"], day=j["day"], tag=j.get("tag"), preset=j.get("preset", False), created=j["created"],
                 rec_start=j.get("rec_start"), params=j.get("params"), log=j.get("log", [])[-80:],
                 events=[slim_event(e) for e in j["events"]], summary=s, curves={}, heat={})
        pts = s.get("points")
        if pts:   # compact the scatter/points arrays
            s["points"] = {k: ([round(x, 6) for x in v] if k in ("lat", "lon") else
                               [[round(y, 3) for y in r] for r in v] if k == "ev" else
                               [round(x, 3) if isinstance(x, float) else x for x in v]) for k, v in pts.items()}
        for c in glob.glob(os.path.join(d, f"{jid}_curves_*.json")):
            r = int(re.search(r"_curves_(\d+)\.json$", c).group(1)); cj = P.load_json(c)
            b["curves"][r] = rnd(dict(dt_s=cj.get("dt_s"), curves=[dict(t=[round(x, 2) for x in cc["t"]], f=[round(x, 2) if x is not None and math.isfinite(x) else None for x in cc["f"]],
                                                                           f0=cc.get("f0"), score=cc.get("score")) for cc in cj["curves"]]))
        for h in glob.glob(os.path.join(d, f"{jid}_heat_*.json")):
            m = re.search(r"_heat_(\d+)_([\d.]+)_([\d.]+)\.json$", h)
            if not m or m.group(2) != "1.5" or float(m.group(3)) != 25.0: continue
            r = int(m.group(1)); hj = P.load_json(h)
            b["heat"][r] = dict(step_m=hj.get("step_m"), lat=[round(x, 6) for x in hj["lat"]], lon=[round(x, 6) for x in hj["lon"]],
                                layers={k: [round(x, 3) if x is not None and x > -1e8 else None for x in v] for k, v in hj["layers"].items()})
        b["summary"] = rnd({k: v for k, v in s.items() if k != "points"}); b["summary"]["points"] = s.get("points")
        fn = f"res_{jid}.json"
        with open(os.path.join(out, fn), "w") as fo: json.dump(b, fo, separators=(",", ":"))
        rows.append(dict(id=jid, file=fn, mode=j["mode"], day=j["day"], tag=j.get("tag"), created=j["created"],
                         n_events=len(j["events"]), truth_rank=s.get("truth_rank"), n_combos=len(s.get("combos") or []),
                         event_ids=[e["id"] for e in j["events"]], event_t0=[e["t0"] for e in j["events"]],
                         heat_ranks=sorted(b["heat"]), curve_ranks=sorted(b["curves"])))
    rows.sort(key=lambda r: -r["created"])
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=os.path.join(APP, "..", "viewer", "site"))
    ap.add_argument("--only", default=""); a = ap.parse_args()
    out = os.path.abspath(a.out); data = os.path.join(out, "data"); os.makedirs(data, exist_ok=True)
    for f in glob.glob(os.path.join(APP, "web_static", "*")):            # viewer front-end
        if os.path.isfile(f): shutil.copy2(f, out)
    only = set(x for x in a.only.split(",") if x)
    metas = [m for m in P.list_projects() if m.get("preset") and (not only or m["id"] in only)]
    idx_path = os.path.join(data, "index.json")
    old = {p["id"]: p for p in (json.load(open(idx_path))["presets"] if os.path.exists(idx_path) and only else [])}
    for m in metas:
        pid = m["id"]; t = time.time(); od = os.path.join(data, pid)
        if os.path.isdir(od): shutil.rmtree(od)
        os.makedirs(od)
        imgs, spec = export_spec(pid, od)
        results = export_results(pid, od)
        ev = P.events(pid)
        used = {}
        for r in results:
            for eid, t0 in zip(r["event_ids"], r["event_t0"]): used[eid] = t0
        for e in ev:
            if e.get("selected"): used[e["id"]] = e["t0"]
        clips = export_audio(pid, od, m, used)
        mm = {k: v for k, v in m.items() if k not in ("source_path",)}
        mm.update(STATUS.get(pid, {})); mm["group"] = GROUP(pid)
        proj = dict(meta=mm, events=[slim_event(e) for e in ev], gaps=P.gap_table([e for e in ev if e.get("selected")]),
                    spec=spec, images=imgs, audio=clips, results=results)
        with open(os.path.join(od, "project.json"), "w") as fo: json.dump(rnd(proj), fo, separators=(",", ":"))
        sz = sum(os.path.getsize(os.path.join(od, x)) for x in os.listdir(od))
        old[pid] = dict(id=pid, name=m["name"], order=m.get("order", 99), group=mm["group"], status=mm.get("status", "solved"),
                        default_tag=m.get("default_tag"), default_mode=m.get("default_mode"), size_bytes=sz)
        print(f"{pid}: {len(imgs)} images, {len(clips)} audio, {len(results)} results, {sz / 1e6:.1f} MB, {time.time() - t:.0f} s", flush=True)
    for pid in list(old):
        if not os.path.isdir(os.path.join(data, pid)): old.pop(pid)
    pres = sorted(old.values(), key=lambda p: (p["order"], p["id"]))
    git = subprocess.run(["git", "-C", APP, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    with open(idx_path, "w") as fo:
        json.dump(dict(generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), app_commit=git, presets=pres), fo, indent=1)
    tot = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(out) for f in fs)
    print(f"total {tot / 1e6:.1f} MB in {out}")


if __name__ == "__main__":
    main()
