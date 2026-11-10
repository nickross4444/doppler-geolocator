"""Build a day store (same layout as scripts/ingest_adsblol.py) from adsb.lol *live API* polls recorded with
examples/ithaca_feederwatch/poll_adsb.py (JSON lines: {"now": ms, "poll_t": s, "ac": [...]}).
Used when the recording is from today and the globe_history daily release is not out yet.
usage: python scripts/ingest_adsblol_live.py polls.jsonl out_dir [types_json]"""
import sys, os, json
import numpy as np, polars as pl
MAX_GAP = 600.0

def main(src, out, types_json):
    types = json.load(open(types_json))
    rows = {}; meta = {}
    for line in open(src):
        try: d = json.loads(line)
        except Exception: continue
        now = (d["now"] if "now" in d else d["poll_t"] * 1000) / 1000.0
        for a in d.get("ac", []):
            if a.get("lat") is None or a.get("seen_pos") is None: continue
            t = round(now - float(a["seen_pos"]), 1)
            ab = a.get("alt_baro"); ab = np.nan if ab in (None, "ground") or isinstance(ab, str) else float(ab)
            if not np.isfinite(ab): continue
            h = a["hex"]
            meta.setdefault(h, dict(reg=a.get("r") or "", type=(a.get("t") or "").upper()))
            vr = a.get("geom_rate", a.get("baro_rate"))
            rows.setdefault(h, {})[t] = (t, a["lat"], a["lon"], ab, a.get("alt_geom", np.nan), a.get("gs", np.nan),
                                         a.get("track", a.get("true_heading", np.nan)), np.nan if vr is None else vr)
    cols = {k: [] for k in ("fid", "t", "lat", "lon", "ab", "ag", "gs", "trk", "vr")}; flights = []; fid = 0
    for h, rr in rows.items():
        A = np.array(sorted(rr.values()), float)
        if A.shape[0] < 3: continue
        cut = np.r_[True, np.diff(A[:, 0]) > MAX_GAP]; seg = np.cumsum(cut) - 1
        for s in np.unique(seg):
            B = A[seg == s]
            if B.shape[0] < 3 or B[-1, 0] - B[0, 0] < 30: continue
            m = meta[h]; desc = (types.get(m["type"]) or {}).get("desc", "")
            cls = "unknown"
            if len(desc) >= 3: cls = "rotor" if desc[0] in "HG" else ("jet" if desc[2] == "J" else "prop")
            flights.append(dict(fid=fid, hex=h, reg=m["reg"], type=m["type"], desc=desc, cls=cls, t_start=float(B[0, 0]),
                                t_end=float(B[-1, 0]), n=int(B.shape[0]), max_alt_ft=float(np.nanmax(B[:, 3]))))
            cols["fid"].append(np.full(B.shape[0], fid, np.int32))
            for k, name in enumerate(("t", "lat", "lon", "ab", "ag", "gs", "trk", "vr")):
                cols[name].append(B[:, k] if name in ("t", "lat", "lon") else B[:, k].astype(np.float32))
            fid += 1
    pd = os.path.join(out, "points"); os.makedirs(pd, exist_ok=True)
    names = dict(fid="fid", t="t", lat="lat", lon="lon", ab="alt_baro_ft", ag="alt_geom_ft", gs="gs_kt", trk="trk", vr="vr_fpm")
    for k in cols: np.save(os.path.join(pd, names[k] + ".npy"), np.concatenate(cols[k]))
    F = pl.DataFrame(flights); c = F["n"].to_numpy(); i1 = np.cumsum(c)
    F.with_columns(pl.Series("i0", i1 - c), pl.Series("i1", i1)).write_parquet(os.path.join(out, "flights.parquet"))
    print(f"{fid} flights, {sum(map(len, cols['t']))} points"); print(F.group_by("cls").len())

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else os.path.join(here, "..", "..", "data", "ref", "icao_aircraft_types.json"))
