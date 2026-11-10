"""List aircraft passes near a camera from adsb.lol live-API polls (examples/*/adsb_live.jsonl).
Heard-CPA time = time of minimum slant range + slant/343 (sound delay), relative to the recording start.
usage: python scripts/live_cam_passes.py polls.jsonl LAT LON ELEV_M REC_START_EPOCH [DUR_S] [MAX_SLANT_KM]"""
import sys, os, json
import numpy as np
from math import radians, cos
HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = json.load(open(os.path.join(HERE, "..", "..", "data", "ref", "icao_aircraft_types.json")))

def cls_of(t):
    desc = (TYPES.get((t or "").upper()) or {}).get("desc", "")
    if len(desc) < 3: return "unknown"
    return "rotor" if desc[0] in "HG" else ("jet" if desc[2] == "J" else "prop")

def passes(src, lat0, lon0, elev0, rec0, dur=4000, max_km=10.0):
    tr = {}; meta = {}
    for line in open(src):
        try: d = json.loads(line)
        except Exception: continue
        now = (d["now"] if "now" in d else d["poll_t"] * 1000) / 1000.0
        for a in d.get("ac", []):
            if a.get("lat") is None or a.get("seen_pos") is None: continue
            ag = a.get("alt_geom", a.get("alt_baro"))
            if not isinstance(ag, (int, float)): continue
            t = round(now - float(a["seen_pos"]), 1)
            tr.setdefault(a["hex"], {})[t] = (t, a["lat"], a["lon"], ag * 0.3048, a.get("gs") or np.nan)
            meta[a["hex"]] = (a.get("r") or "", a.get("t") or "", a.get("flight", "").strip())
    out = []
    kx = 111320.0 * cos(radians(lat0))
    for h, rr in tr.items():
        A = np.array(sorted(rr.values()))
        A = A[(A[:, 0] > rec0 - 120) & (A[:, 0] < rec0 + dur + 120)]
        if len(A) < 3: continue
        tt = np.arange(A[0, 0], A[-1, 0], 1.0)
        if len(tt) < 3: continue
        x = (np.interp(tt, A[:, 0], A[:, 2]) - lon0) * kx; y = (np.interp(tt, A[:, 0], A[:, 1]) - lat0) * 111320.0
        z = np.interp(tt, A[:, 0], A[:, 3]) - elev0
        s = np.sqrt(x * x + y * y + z * z); i = int(np.argmin(s))
        if s[i] > max_km * 1000: continue
        gap = np.max(np.diff(A[:, 0])) if len(A) > 1 else 0
        r, t, fl = meta[h]
        out.append(dict(hex=h, reg=r, type=t, cls=cls_of(t), call=fl, slant_km=s[i] / 1000, alt_m=z[i] + elev0,
                        gs_kt=float(np.nanmedian(A[:, 4])), t_heard=tt[i] + s[i] / 343.0 - rec0, max_gap_s=float(gap),
                        edge=i in (0, len(tt) - 1)))
    return sorted(out, key=lambda r: r["t_heard"])

if __name__ == "__main__":
    a = sys.argv
    for r in passes(a[1], float(a[2]), float(a[3]), float(a[4]), float(a[5]), float(a[6]) if len(a) > 6 else 4000,
                    float(a[7]) if len(a) > 7 else 10.0):
        if -60 < r["t_heard"] < (float(a[6]) if len(a) > 6 else 4000) + 60:
            print(f"{r['t_heard']:7.0f}s  {r['slant_km']:5.2f} km  alt {r['alt_m']:6.0f} m  {r['gs_kt']:4.0f} kt  {r['cls']:7s} {r['type']:5s} {r['reg']:8s} {r['call']:8s} {r['hex']} gap {r['max_gap_s']:.0f}{' EDGE' if r['edge'] else ''}")
