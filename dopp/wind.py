"""Historical winds aloft for the optional wind correction (OFF by default: the video's method uses ADS-B
ground velocity unchanged).

Source: Open-Meteo historical-forecast API (pressure-level winds 1000..200 hPa + geopotential heights, hourly,
best-available model incl. ECMWF IFS/ERA5-class analyses), falling back to the ERA5 archive (10/100 m winds)
when pressure levels are unavailable. Cached per (day, 0.5 deg cell) under data/wind/.
"""
import os, json, math, urllib.request, functools
import numpy as np
from datetime import datetime, timezone

CACHE = os.path.abspath(os.environ.get("DOPP_WIND") or os.path.join(os.path.dirname(__file__), "..", "data", "wind"))
LEVELS = (1000, 925, 850, 700, 600, 500, 400, 300, 250, 200)

def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "doppler-geoloc/1.0"}), timeout=60) as r:
        return json.loads(r.read())

def _cell(x, res=0.5): return round(round(x / res) * res, 2)

@functools.lru_cache(maxsize=4096)
def profile(day, lat, lon):
    """Hourly wind profile at a 0.5 deg cell: dict(t=[epoch], z=[[height_m per level]], u=[[...]], v=[[...]])."""
    lat, lon = _cell(lat), _cell(lon)
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, f"{day}_{lat:+.2f}_{lon:+.2f}.json")
    if os.path.exists(f): return json.load(open(f))
    vs = []
    for L in LEVELS: vs += [f"wind_speed_{L}hPa", f"wind_direction_{L}hPa", f"geopotential_height_{L}hPa"]
    vs += ["wind_speed_10m", "wind_direction_10m", "wind_speed_100m", "wind_direction_100m"]
    q = f"latitude={lat}&longitude={lon}&start_date={day}&end_date={day}&wind_speed_unit=ms&timezone=GMT&hourly=" + ",".join(vs)
    src = "open-meteo historical-forecast"
    try:
        d = _get("https://historical-forecast-api.open-meteo.com/v1/forecast?" + q)["hourly"]
        if all(x is None for x in d.get("wind_speed_850hPa", [None])): raise ValueError("no pressure levels")
    except Exception:
        q = f"latitude={lat}&longitude={lon}&start_date={day}&end_date={day}&wind_speed_unit=ms&timezone=GMT&models=era5&hourly=" \
            "wind_speed_10m,wind_direction_10m,wind_speed_100m,wind_direction_100m"
        d = _get("https://archive-api.open-meteo.com/v1/archive?" + q)["hourly"]; src = "open-meteo ERA5 archive (10/100 m only)"
    t = [datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() for s in d["time"]]
    have_pl = any(d.get(f"wind_speed_{L}hPa", [None])[0] is not None for L in LEVELS)
    Z, U, V = [], [], []
    for i in range(len(t)):
        z, u, v = [], [], []
        for (h, sp, di) in [(10.0, "wind_speed_10m", "wind_direction_10m"), (100.0, "wind_speed_100m", "wind_direction_100m")] + \
                [(None, f"wind_speed_{L}hPa", f"wind_direction_{L}hPa") for L in LEVELS]:
            if sp not in d or d[sp][i] is None or d[di][i] is None: continue
            if h is not None and have_pl: continue     # 10/100 m are AGL; with pressure levels use those only
            if h is None:
                L = sp.split("_")[-1][:-3]; h = d.get(f"geopotential_height_{L}hPa", [None] * len(t))[i]
                if h is None: continue
                h = float(h)
            s_, dd = float(d[sp][i]), math.radians(float(d[di][i]))
            z.append(h); u.append(-s_ * math.sin(dd)); v.append(-s_ * math.cos(dd))   # meteorological 'from' direction
        o = np.argsort(z); Z.append(np.asarray(z)[o].tolist()); U.append(np.asarray(u)[o].tolist()); V.append(np.asarray(v)[o].tolist())
    out = dict(t=t, z=Z, u=U, v=V, source=src, lat=lat, lon=lon)
    json.dump(out, open(f, "w")); return out

def wind_at(lat, lon, alt_m, t_abs):
    """(u_east, v_north) m/s at a position (alt MSL ~ geopotential height) and UTC epoch; linear in time and height."""
    day = datetime.fromtimestamp(t_abs, timezone.utc).strftime("%Y-%m-%d")
    P = profile(day, _cell(lat), _cell(lon))
    t = np.asarray(P["t"]); i = int(np.clip(np.searchsorted(t, t_abs) - 1, 0, len(t) - 2)); a = float(np.clip((t_abs - t[i]) / 3600.0, 0, 1))
    def at(k):
        z = np.asarray(P["z"][k]);
        if z.size == 0: return 0.0, 0.0
        return float(np.interp(alt_m, z, P["u"][k])), float(np.interp(alt_m, z, P["v"][k]))
    u0, v0 = at(i); u1, v1 = at(i + 1)
    return (u0 * (1 - a) + u1 * a, v0 * (1 - a) + v1 * a)

def flight_wind(store, fid, t_abs):
    """Wind at the flight's own position/altitude at t_abs (nearest valid track sample)."""
    tr = store.track(int(fid))
    k = int(np.clip(np.searchsorted(tr["t"], t_abs), 0, tr["t"].size - 1))
    if not np.isfinite(tr["lat"][k]): return (0.0, 0.0)
    return wind_at(float(tr["lat"][k]), float(tr["lon"][k]), float(tr["alt_m"][k]), float(t_abs))
