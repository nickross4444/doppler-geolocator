"""Backfill full-resolution tracks from adsb.lol's per-aircraft day traces (https://adsb.lol/data/traces/XX/trace_full_HEX.json,
readsb/tar1090 format) for every aircraft seen in the live-API polls within RADIUS_NM of the camera during [t0, t1].
Writes JSON lines in the same format as the live polls ({"now": ms, "ac": [...]}, seen_pos=0) so that
scripts/ingest_adsblol_live.py can merge both.  Fills gaps in the polls (rate limits, box pauses).
usage: python scripts/adsblol_traces.py polls.jsonl LAT LON RADIUS_NM T0 T1 out.jsonl"""
import sys, json, gzip, time, urllib.request, math
from concurrent.futures import ThreadPoolExecutor

def hexes(src, lat0, lon0, rad_nm, t0, t1):
    out = set(); kx = math.cos(math.radians(lat0))
    for line in open(src):
        try: d = json.loads(line)
        except Exception: continue
        now = (d["now"] if "now" in d else d["poll_t"] * 1000) / 1000.0
        if now < t0 - 600 or now > t1 + 600: continue
        for a in d.get("ac", []):
            if a.get("lat") is None: continue
            dn = math.hypot((a["lat"] - lat0) * 60, (a["lon"] - lon0) * 60 * kx)
            if dn <= rad_nm: out.add(a["hex"])
    return sorted(out)

def fetch(h):
    url = f"https://adsb.lol/data/traces/{h[-2:]}/trace_full_{h}.json"
    for k in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "doppler-research/1.0", "Accept-Encoding": "gzip"})
            raw = urllib.request.urlopen(req, timeout=30).read()
            try: raw = gzip.decompress(raw)
            except OSError: pass
            return json.loads(raw)
        except Exception as e:
            err = e; time.sleep(1.5 * (k + 1))
    print("fail", h, err, flush=True); return None

def main(src, lat0, lon0, rad, t0, t1, out):
    hs = hexes(src, lat0, lon0, rad, t0, t1); print(len(hs), "aircraft", flush=True)
    n = 0
    with ThreadPoolExecutor(4) as ex, open(out, "w") as f:
        for h, d in zip(hs, ex.map(fetch, hs)):
            if not d: continue
            ts = d["timestamp"]
            for p in d["trace"]:
                t = ts + p[0]
                if t < t0 - 900 or t > t1 + 900: continue
                ab = p[3]
                a = dict(hex=h, r=d.get("r", ""), t=d.get("t", ""), lat=p[1], lon=p[2], alt_baro=ab, gs=p[4], track=p[5],
                         seen_pos=0.0, src="trace")
                if len(p) > 7 and p[7] is not None: a["baro_rate"] = p[7]
                if len(p) > 10 and p[10] is not None: a["alt_geom"] = p[10]
                if len(p) > 11 and p[11] is not None: a["geom_rate"] = p[11]
                f.write(json.dumps(dict(now=t * 1000.0, ac=[a])) + "\n"); n += 1
    print(n, "trace points", flush=True)

if __name__ == "__main__":
    a = sys.argv; main(a[1], float(a[2]), float(a[3]), float(a[4]), float(a[5]), float(a[6]), a[7])
