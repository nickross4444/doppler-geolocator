"""DEM from public AWS Terrarium tiles (s3://elevation-tiles-prod, no auth).
Default zoom 7 (~1 km/pixel at 40N) to match the author's `dem_mosaic_1km`; finer zooms on request."""
import os, math, io, urllib.request, threading
import numpy as np
from PIL import Image

CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "dem_tiles")
URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
_lock = threading.Lock(); _mem = {}

def _tile(z, x, y):
    key = (z, x, y)
    if key in _mem: return _mem[key]
    p = os.path.join(CACHE, str(z), str(x), f"{y}.png")
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            urllib.request.urlretrieve(URL.format(z=z, x=x, y=y), p)
        except Exception:
            a = np.zeros((256, 256), np.float32); _mem[key] = a; return a
    im = np.asarray(Image.open(p).convert("RGB")).astype(np.float32)
    a = (im[..., 0] * 256 + im[..., 1] + im[..., 2] / 256) - 32768
    with _lock: _mem[key] = a
    return a

def elevation(lat, lon, z=7):
    """Bilinear ground elevation (m, MSL) at arrays of lat/lon."""
    lat = np.atleast_1d(np.asarray(lat, float)); lon = np.atleast_1d(np.asarray(lon, float))
    n = 2 ** z
    xf = (lon + 180) / 360 * n * 256
    lr = np.radians(lat)
    yf = (1 - np.log(np.tan(lr) + 1 / np.cos(lr)) / math.pi) / 2 * n * 256
    xf -= 0.5; yf -= 0.5
    x0 = np.floor(xf).astype(np.int64); y0 = np.floor(yf).astype(np.int64)
    fx = xf - x0; fy = yf - y0
    out = np.zeros(lat.size)
    def val(px, py):
        tx, ty = px // 256, py // 256
        v = np.empty(px.size)
        for (a, b) in set(zip(tx.tolist(), ty.tolist())):
            m = (tx == a) & (ty == b)
            v[m] = _tile(z, a % n, b)[py[m] % 256, px[m] % 256]
        return v
    v00 = val(x0, y0); v10 = val(x0 + 1, y0); v01 = val(x0, y0 + 1); v11 = val(x0 + 1, y0 + 1)
    out = v00 * (1 - fx) * (1 - fy) + v10 * fx * (1 - fy) + v01 * (1 - fx) * fy + v11 * fx * fy
    return np.maximum(out, 0.0)  # water/below-sea clamp: observer at ground level >= 0 m
