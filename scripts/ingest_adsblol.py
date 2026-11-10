"""Ingest an adsb.lol globe_history daily release (split tar) into a compact per-day store.

Output dir: <out>/  points.npz  (fid,t,lat,lon,alt_baro_ft,alt_geom_ft,gs_kt,trk,vr_fpm)  sorted by fid,t
                    flights.parquet (fid,hex,reg,type,desc,cls,t_start,t_end,i0,i1,max_alt_ft)
Concept (readsb trace layout, alt "ground" handling) follows the MIT-licensed
AlanBacker/audio-flyover-geolocation adsb.py; code is our own.
"""
import sys, os, json, subprocess, tarfile, time
import numpy as np, polars as pl
from multiprocessing import Pool

BBOX = tuple(map(float, os.environ["INGEST_BBOX"].split(","))) if os.environ.get("INGEST_BBOX") else (23.5, 50.5, -126.0, -65.5)   # CONUS + margin (lat0,lat1,lon0,lon1)
MAX_GAP = 600.0                       # s; split hex trace into flights at gaps > 10 min

def parse(item):
    name, raw = item
    try:
        if raw[:2] == b"\x1f\x8b":
            import gzip; raw = gzip.decompress(raw)
        o = json.loads(raw)
    except Exception:
        return None
    tr = o.get("trace") or []
    if len(tr) < 3: return None
    base = float(o.get("timestamp", 0))
    n = len(tr)
    t = np.empty(n); lat = np.empty(n); lon = np.empty(n); ab = np.empty(n); ag = np.empty(n)
    gs = np.empty(n); trk = np.empty(n); vr = np.empty(n); newleg = np.zeros(n, bool)
    def f(v): return np.nan if v is None or isinstance(v, str) else float(v)
    for i, e in enumerate(tr):
        t[i] = base + e[0]; lat[i] = f(e[1]); lon[i] = f(e[2])
        ab[i] = np.nan if e[3] == "ground" else f(e[3])
        gs[i] = f(e[4]); trk[i] = f(e[5]); vr[i] = f(e[7]) if len(e) > 7 else np.nan
        ag[i] = f(e[10]) if len(e) > 10 else np.nan
        fl = e[6] if len(e) > 6 and isinstance(e[6], int) else 0
        newleg[i] = bool(fl & 2)
    m = (np.isfinite(lat) & (lat >= BBOX[0]) & (lat <= BBOX[1]) & (lon >= BBOX[2]) & (lon <= BBOX[3])
         & np.isfinite(ab))
    if m.sum() < 3: return None
    idx = np.flatnonzero(m)
    tt = t[idx]
    cut = np.zeros(idx.size, bool); cut[0] = True
    cut[1:] = (np.diff(tt) > MAX_GAP) | newleg[idx][1:]
    seg = np.cumsum(cut) - 1
    meta = dict(hex=o.get("icao", name), reg=o.get("r") or "", type=(o.get("t") or "").upper(),
                descname=o.get("desc") or "")
    return meta, seg.astype(np.int16), tt, lat[idx], lon[idx], ab[idx], ag[idx], gs[idx], trk[idx], vr[idx]

def stream(parts):
    p = subprocess.Popen(["cat", *parts], stdout=subprocess.PIPE, bufsize=1 << 22)
    tf = tarfile.open(fileobj=p.stdout, mode="r|")
    for m in tf:
        if m.isfile() and "trace_full_" in m.name:
            yield m.name.rsplit("_", 1)[-1].split(".")[0], tf.extractfile(m).read()

def main(out, parts, types_json):
    os.makedirs(out, exist_ok=True)
    types = json.load(open(types_json))
    cols = {k: [] for k in ("fid", "t", "lat", "lon", "ab", "ag", "gs", "trk", "vr")}
    flights = []; fid = 0; t0 = time.time(); nfile = 0
    with Pool(7) as pool:
        for r in pool.imap_unordered(parse, stream(parts), chunksize=64):
            nfile += 1
            if nfile % 10000 == 0: print(f"{nfile} files {fid} flights {time.time()-t0:.0f}s", flush=True)
            if r is None: continue
            meta, seg, *arrs = r
            for s in np.unique(seg):
                k = seg == s
                if k.sum() < 3: continue
                tt = arrs[0][k]
                if tt[-1] - tt[0] < 30: continue
                desc = (types.get(meta["type"]) or {}).get("desc", "")
                cls = "unknown"
                if len(desc) >= 3:
                    cls = "rotor" if desc[0] in "HG" else ("jet" if desc[2] == "J" else "prop")
                flights.append(dict(fid=fid, hex=meta["hex"], reg=meta["reg"], type=meta["type"], desc=desc,
                                    cls=cls, t_start=float(tt[0]), t_end=float(tt[-1]), n=int(k.sum()),
                                    max_alt_ft=float(np.nanmax(arrs[3][k]))))
                cols["fid"].append(np.full(k.sum(), fid, np.int32))
                for name, a in zip(("t", "lat", "lon", "ab", "ag", "gs", "trk", "vr"), arrs):
                    cols[name].append(a[k] if name in ("t", "lat", "lon") else a[k].astype(np.float32))
                fid += 1
    print("concatenating", flush=True)
    pd = os.path.join(out, "points"); os.makedirs(pd, exist_ok=True)
    names = dict(fid="fid", t="t", lat="lat", lon="lon", ab="alt_baro_ft", ag="alt_geom_ft", gs="gs_kt", trk="trk", vr="vr_fpm")
    npts = 0
    for k in list(cols):
        a = np.concatenate(cols[k]); cols[k] = None; npts = a.size
        np.save(os.path.join(pd, names[k] + ".npy"), a); del a
    F = pl.DataFrame(flights)
    counts = F["n"].to_numpy(); i1 = np.cumsum(counts); i0 = i1 - counts
    F = F.with_columns(pl.Series("i0", i0), pl.Series("i1", i1))
    F.write_parquet(os.path.join(out, "flights.parquet"))
    print(f"done {nfile} files, {fid} flights, {npts} points, {time.time()-t0:.0f}s", flush=True)
    print(F.group_by("cls").len())

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[3:], sys.argv[2])
