"""Poll the free adsb.lol live API while the SoCal cams are recorded (live substitute for the next-day
globe_history release). One poll centred between Channel Islands Harbor (Oxnard) and Venice Beach, 60 nm radius,
so each camera has >= 40 nm coverage. Writes one JSON object per poll: {"now": epoch_s, "ac": [...], "poll_t": ...}."""
import json, time, sys, urllib.request
import os
LAT, LON, NM, PERIOD = float(os.environ["LAT"]), float(os.environ["LON"]), float(os.environ.get("NM", 40)), float(os.environ.get("PERIOD", 2))
DUR = float(sys.argv[1]); out = open(sys.argv[2], "a")
t_end = time.time() + DUR; back = PERIOD
while time.time() < t_end:
    t0 = time.time()
    try:
        with urllib.request.urlopen(f"https://api.adsb.lol/v2/point/{LAT}/{LON}/{NM}", timeout=10) as r:
            d = json.loads(r.read()); d["poll_t"] = t0; out.write(json.dumps(d) + "\n"); out.flush(); back = PERIOD
    except Exception as e:
        print(time.strftime('%H:%M:%S'), "err", e, flush=True); back = min(back * 1.5, 10)
    time.sleep(max(0.0, back - (time.time() - t0)))
