"""Flight-data providers and per-day track store.

Primary provider: adsb.lol globe_history (ODbL), ingested by scripts/ingest_adsblol.py.
FR24Provider is a stub for the author's original source (pyfr24, paid API).
"""
import os, json, functools
import numpy as np, polars as pl
from . import FT, KT

DATA = os.path.abspath(os.environ.get("DOPP_DAYS") or os.path.join(os.path.dirname(__file__), "..", "data", "days"))
GEOID_N_M = -30.0   # mean geoid undulation over CONUS (EGM96 ~ -8..-35 m); MSL = HAE - N
MAX_INTERP_GAP_S = 60.0   # 1 s resampling only across raw gaps <= this ("valid" flag in author's layer)

class FlightProvider:
    name = "base"
    def available_days(self): raise NotImplementedError
    def load_day(self, day): raise NotImplementedError

class FR24Provider(FlightProvider):
    """Stub: the video used Flightradar24 via `pyfr24` (paid API). Implement load_day() to
    write the same DayStore layout (flights.parquet + points/*.npy) and everything else works."""
    name = "fr24"
    def available_days(self): return []
    def load_day(self, day): raise NotImplementedError("FR24/pyfr24 provider not configured (needs paid API key)")

class AdsbLolProvider(FlightProvider):
    name = "adsb.lol"
    def __init__(self, root=DATA): self.root = root
    def available_days(self):
        if not os.path.isdir(self.root): return []
        return sorted(d for d in os.listdir(self.root) if os.path.exists(os.path.join(self.root, d, "flights.parquet")))
    def load_day(self, day): return DayStore(os.path.join(self.root, day))

PROVIDERS = {"adsb.lol": AdsbLolProvider(), "fr24": FR24Provider()}

class DayStore:
    COLS = ("fid", "t", "lat", "lon", "alt_baro_ft", "alt_geom_ft", "gs_kt", "trk", "vr_fpm")
    def __init__(self, d):
        self.dir = d
        self.flights = pl.read_parquet(os.path.join(d, "flights.parquet"))
        pd = os.path.join(d, "points")
        if not os.path.isdir(pd):          # one-time conversion npz -> mmap-able npy
            os.makedirs(pd, exist_ok=True)
            z = np.load(os.path.join(d, "points.npz"))
            for k in self.COLS: np.save(os.path.join(pd, k + ".npy"), z[k])
        self.P = {k: np.load(os.path.join(pd, k + ".npy"), mmap_mode="r") for k in self.COLS}
        F = self.flights
        self.i0 = F["i0"].to_numpy(); self.i1 = F["i1"].to_numpy()
        self.cls = F["cls"].to_numpy(); self.t_start = F["t_start"].to_numpy(); self.t_end = F["t_end"].to_numpy()

    def raw(self, fid):
        a, b = int(self.i0[fid]), int(self.i1[fid])
        return {k: np.asarray(self.P[k][a:b]) for k in self.COLS if k != "fid"}

    @functools.lru_cache(maxsize=20000)
    def track(self, fid):
        """1 s resampled track (the author's `flight_X_track_1sec`): t, lat, lon, alt_m (MSL), gs (m/s),
        trk (deg), vs (m/s), valid. Linear interpolation; samples inside raw gaps > 60 s are invalid."""
        r = self.raw(fid)
        t = r["t"]
        o = np.argsort(t, kind="stable"); t = t[o]
        keep = np.r_[True, np.diff(t) > 0.05]; o = o[keep]; t = t[keep]
        alt_ft = np.where(np.isfinite(r["alt_geom_ft"][o]), r["alt_geom_ft"][o] - GEOID_N_M / FT, r["alt_baro_ft"][o])
        tt = np.arange(np.ceil(t[0]), np.floor(t[-1]) + 1)
        if tt.size < 2: tt = np.array([t[0], t[-1]])
        def it(v):
            v = np.asarray(v, float); m = np.isfinite(v)
            return np.interp(tt, t[m], v[m]) if m.sum() >= 2 else np.full(tt.size, np.nan)
        trk = np.degrees(np.unwrap(np.radians(np.nan_to_num(r["trk"][o].astype(float)))))
        gap = np.interp(tt, t[1:], np.diff(t)) if t.size > 1 else np.zeros(tt.size)
        idx = np.clip(np.searchsorted(t, tt) , 1, t.size - 1)
        gap = t[idx] - t[idx - 1]
        return dict(t=tt, lat=it(r["lat"][o]), lon=it(r["lon"][o]), alt_m=it(alt_ft) * FT,
                    gs=it(r["gs_kt"][o]) * KT, trk=np.mod(it(trk), 360), vs=it(r["vr_fpm"][o]) * FT / 60,
                    valid=gap <= MAX_INTERP_GAP_S)

    def meta(self, fid):
        row = self.flights.row(int(fid), named=True)
        return {k: row[k] for k in ("fid", "hex", "reg", "type", "desc", "cls")}

    def fids_active(self, t0, t1, classes):
        m = (self.t_end >= t0) & (self.t_start <= t1) & np.isin(self.cls, list(classes))
        return np.flatnonzero(m)
