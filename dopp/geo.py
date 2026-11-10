import numpy as np
R_EARTH = 6371008.8

def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1; dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(np.clip(a, 0, 1)))

def enu(lat, lon, lat0, lon0):
    """Local equirectangular metres around (lat0, lon0); accurate to <0.1% within ~20 km."""
    x = np.radians(np.asarray(lon) - lon0) * R_EARTH * np.cos(np.radians(lat0))
    y = np.radians(np.asarray(lat) - lat0) * R_EARTH
    return x, y

GRID_M = 200.0
DLAT = GRID_M / 111320.0

def grid_ids_near(lat, lon, radius_m, step_m=GRID_M):
    """All global-grid points (row, col) within radius of any (lat, lon) sample.
    Global grid: rows every step_m in latitude; along each row, columns every step_m
    (lon step = step/(111320 cos(row_lat))). Deterministic so the same point id is
    produced from every flight (needed to join event lists per point)."""
    dlat = step_m / 111320.0
    rows = np.arange(np.floor((np.min(lat) * 111320 - radius_m) / step_m),
                     np.ceil((np.max(lat) * 111320 + radius_m) / step_m) + 1).astype(np.int64)
    return rows, dlat

def row_lat(row, step_m=GRID_M):
    return row * step_m / 111320.0

def col_dlon(row, step_m=GRID_M):
    return step_m / (111320.0 * np.cos(np.radians(row_lat(row, step_m))))

def point_latlon(row, col, step_m=GRID_M):
    return row_lat(row, step_m), col * col_dlon(row, step_m)

def points_in_corridor(lat, lon, rmax, step_m=GRID_M):
    """Grid points within horizontal rmax of a polyline given by dense samples (≤ step spacing)."""
    from scipy.spatial import cKDTree
    lat0 = float(np.mean(lat)); lon0 = float(np.mean(lon))
    x, y = enu(lat, lon, lat0, lon0)
    tree = cKDTree(np.c_[x, y])
    r0 = int(np.floor((np.min(lat) * 111320 - rmax) / step_m)); r1 = int(np.ceil((np.max(lat) * 111320 + rmax) / step_m))
    out_r, out_c = [], []
    for r in range(r0, r1 + 1):
        la = r * step_m / 111320.0
        dl = step_m / (111320.0 * np.cos(np.radians(la)))
        # longitude span of samples within rmax of this row
        near = np.abs((lat - la) * 111320.0) <= rmax
        if not near.any(): continue
        lo_min = lon[near].min() - rmax / (111320 * np.cos(np.radians(la))) ; lo_max = lon[near].max() + rmax / (111320 * np.cos(np.radians(la)))
        cols = np.arange(int(np.floor(lo_min / dl)), int(np.ceil(lo_max / dl)) + 1)
        px, py = enu(np.full(cols.size, la), cols * dl, lat0, lon0)
        d, _ = tree.query(np.c_[px, py], distance_upper_bound=rmax)
        k = np.isfinite(d)
        out_r.append(np.full(k.sum(), r, np.int64)); out_c.append(cols[k])
    if not out_r: return np.zeros(0, np.int64), np.zeros(0, np.int64)
    return np.concatenate(out_r), np.concatenate(out_c)
