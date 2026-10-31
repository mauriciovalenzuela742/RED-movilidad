"""Road-segment reference layer built from the DTPM GTFS feed and the Copernicus DEM.

    python -m pulso.segments --out data/derived [--gtfs DIR] [--dem DIR]

Outputs (all in --out):
  shapes.parquet            one row per GTFS shape_id (+ WKB lon/lat polyline, used by project_points)
  segments.parquet          shapes cut into ~100 m segments, with elevation, grade and phys_id
  shape_stops.parquet       stops served along each shape (one representative trip per shape)
  physical_segments.parquet street-level segments shared by several shapes (same street, same direction)

Library entry points:
  project_points(shape_id, lon, lat, prev_m=None)   distance along shape + signed offset (m)
  project_dataframe(df, ...)                         batch helper over a DataFrame
  Dem, smooth_profile, cut_boundaries, build_segments, assign_phys_ids   building blocks (testable)

All metric work is done in UTM 19S (EPSG:32719). Stored geometries are lon/lat (EPSG:4326).
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyproj
import shapely
from shapely import STRtree

UTM = "EPSG:32719"
SEG_LEN_M = 100.0
MIN_TAIL_M = 10.0  # a final remainder shorter than this is merged into the previous segment
PROFILE_STEP_M = 20.0  # spacing of the DEM profile samples along a shape
MEDIAN_WIN = 11  # samples -> 220 m running median (kills bridges/underpasses/spikes)
MEAN_WIN = 11  # samples -> 220 m running mean applied after the median
GRADE_CLIP_PCT = 15.0
STOP_FAR_M = 50.0
OVERLAP_SAMPLE_M = 25.0
OVERLAP_RADIUS_M = 20.0
OVERLAP_MIN_ALONG_M = 500.0  # two parts of a shape closer than 20 m but >500 m apart along it
OVERLAP_MIN_LEN_M = 100.0  # shape counts as self-overlapping above this much overlapped length
PHYS_MAX_DIST_M = 60.0
PHYS_MAX_LATERAL_M = 20.0
PHYS_MAX_BEARING_DEG = 30.0

_FWD = pyproj.Transformer.from_crs("EPSG:4326", UTM, always_xy=True)
_INV = pyproj.Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
_GEOD = pyproj.Geod(ellps="WGS84")


def to_utm(lon, lat):
    x, y = _FWD.transform(np.asarray(lon, float), np.asarray(lat, float))
    return np.asarray(x), np.asarray(y)


def to_lonlat(x, y):
    lon, lat = _INV.transform(np.asarray(x, float), np.asarray(y, float))
    return np.asarray(lon), np.asarray(lat)


# --------------------------------------------------------------------------- shape_id parsing
_HYPHEN_RE = re.compile(r"^(?P<route>[A-Z]+\d*[A-Z]?)-(?P<dir>[IR])$")
_MAIN_RE = re.compile(
    r"^(?P<route>[A-Z]?\d{2,3})(?P<variant>[a-z])?(?P<pre>[A-HJ-QS-Z]*?)(?P<dir>[IR])(?P<tail>.*)$"
)


def parse_shape_id(sid: str) -> dict:
    """Split a GTFS shape_id into route code, variant, direction letter and qualifiers.

    Patterns found in the DTPM feed (see docs/segmentos.md):
      101I, B01R, 201eR, 105cI   route code (+ lowercase variant c/e) + direction letter
      109NI, B02NI               an uppercase qualifier BEFORE the direction (N)
      101IPM, 426IPRN, 428eITNOC qualifier AFTER the direction (PM, PT, PRN, TNOC, PMPT, N, PM2)
      L1-I, MTN-R, L4A-I         metro-like codes with a hyphen: code-direction
    Direction letter I = ida, R = regreso. Unparsable ids get None fields.
    """
    m = _HYPHEN_RE.match(sid)
    if m:
        return dict(route_code=m["route"], variant=None, pre_qualifier=None,
                    direction_letter=m["dir"], tail_qualifier=None, id_pattern="hyphen")
    m = _MAIN_RE.match(sid)
    if m:
        return dict(route_code=m["route"], variant=m["variant"], pre_qualifier=m["pre"] or None,
                    direction_letter=m["dir"], tail_qualifier=m["tail"] or None, id_pattern="main")
    return dict(route_code=None, variant=None, pre_qualifier=None, direction_letter=None,
                tail_qualifier=None, id_pattern="unparsed")


# --------------------------------------------------------------------------- geometry of one shape
class ShapeIndex:
    """A polyline in UTM metres with cumulative distances and fast point projection."""

    def __init__(self, shape_id, x, y):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        keep = np.ones(len(x), bool)
        keep[1:] = (np.hypot(np.diff(x), np.diff(y)) > 1e-6)  # drop duplicate consecutive points
        x, y = x[keep], y[keep]
        if len(x) < 2:
            raise ValueError(f"shape {shape_id}: fewer than 2 distinct points")
        self.shape_id = shape_id
        self.x, self.y = x, y
        d = np.hypot(np.diff(x), np.diff(y))
        self.plen = d
        self.cum = np.concatenate([[0.0], np.cumsum(d)])
        self.length = float(self.cum[-1])
        self.ux, self.uy = np.diff(x) / d, np.diff(y) / d
        self.n_seg = n_segments(self.length)
        self._tree = None

    @classmethod
    def from_lonlat(cls, shape_id, lon, lat):
        x, y = to_utm(lon, lat)
        return cls(shape_id, x, y)

    def at(self, s):
        """UTM coordinates at distances s along the shape (clamped)."""
        s = np.clip(np.asarray(s, float), 0.0, self.length)
        return np.interp(s, self.cum, self.x), np.interp(s, self.cum, self.y)

    def _piece_proj(self, px, py, idx):
        """Projection of points onto pieces idx (same-shape arrays). -> dist, signed offset."""
        ax, ay = self.x[idx], self.y[idx]
        t = (px - ax) * self.ux[idx] + (py - ay) * self.uy[idx]
        t = np.clip(t, 0.0, self.plen[idx])
        qx, qy = ax + t * self.ux[idx], ay + t * self.uy[idx]
        # signed offset: positive when the point is to the LEFT of the direction of travel
        off = (px - qx) * (-self.uy[idx]) + (py - qy) * self.ux[idx]
        dist = np.hypot(px - qx, py - qy)
        return self.cum[idx] + t, np.where(off >= 0, dist, -dist)

    def _get_tree(self):
        if self._tree is None:
            lines = shapely.linestrings(
                np.stack([np.column_stack([self.x[:-1], self.y[:-1]]),
                          np.column_stack([self.x[1:], self.y[1:]])], axis=1))
            self._tree = STRtree(lines)
        return self._tree

    def project_nearest(self, px, py):
        """Unconstrained nearest-point projection (fast, vectorised)."""
        px, py = np.asarray(px, float), np.asarray(py, float)
        idx = self._get_tree().nearest(shapely.points(px, py))
        return self._piece_proj(px, py, np.asarray(idx))

    def _best_in_range(self, px, py, lo, hi):
        i0 = max(int(np.searchsorted(self.cum, lo, side="right")) - 1, 0)
        i1 = min(int(np.searchsorted(self.cum, hi, side="left")), len(self.plen))
        if i1 <= i0:
            i0, i1 = 0, len(self.plen)
        idx = np.arange(i0, i1)
        d, o = self._piece_proj(px, py, idx)
        k = int(np.argmin(np.abs(o)))
        return d[k], o[k]

    def project_sequential(self, px, py, prev_m=None, back_tol_m=30.0, window_m=3000.0, far_m=150.0):
        """Project an ordered sequence of points so that the distance never decreases.

        Each point is searched only in [prev - back_tol, prev + window] along the shape; if the best
        offset there exceeds far_m the search widens to everything ahead of prev - back_tol. The
        returned distance is clamped to be >= the previous one (GPS jitter backwards is absorbed).
        With prev_m=None the first point is projected without constraint.
        """
        px, py = np.asarray(px, float), np.asarray(py, float)
        n = len(px)
        dist, off = np.empty(n), np.empty(n)
        cur = prev_m
        for i in range(n):
            if not (np.isfinite(px[i]) and np.isfinite(py[i])):
                dist[i] = off[i] = np.nan
                continue
            if cur is None:
                d, o = self._best_in_range(px[i], py[i], 0.0, self.length)
            else:
                d, o = self._best_in_range(px[i], py[i], cur - back_tol_m, cur + window_m)
                if abs(o) > far_m:
                    d, o = self._best_in_range(px[i], py[i], cur - back_tol_m, self.length)
                d = max(d, cur)
            dist[i], off[i] = d, o
            cur = d
        return dist, off

    # -- self-overlap diagnostics
    def self_overlap_m(self):
        """Length (m) of the shape that lies within 20 m of a part >500 m away along it, roughly parallel
        or antiparallel (so plain crossings at intersections do not count)."""
        n = int(self.length // OVERLAP_SAMPLE_M) + 1
        s = np.linspace(0, self.length, n)
        x, y = self.at(s)
        if n < 3:
            return 0.0
        hx, hy = np.gradient(x), np.gradient(y)
        h = np.hypot(hx, hy)
        h[h == 0] = 1
        hx, hy = hx / h, hy / h
        pts = shapely.points(x, y)
        i, j = STRtree(pts).query(pts, predicate="dwithin", distance=OVERLAP_RADIUS_M)
        m = (s[j] - s[i]) > OVERLAP_MIN_ALONG_M
        i, j = i[m], j[m]
        par = np.abs(hx[i] * hx[j] + hy[i] * hy[j]) > np.cos(np.radians(30))
        return float(len(np.unique(i[par])) * OVERLAP_SAMPLE_M)


def n_segments(length_m: float, step: float = SEG_LEN_M) -> int:
    n = int(np.ceil(length_m / step - 1e-9))
    rem = length_m - (n - 1) * step
    if n > 1 and rem < MIN_TAIL_M:
        n -= 1
    return max(n, 1)


def cut_boundaries(length_m: float, step: float = SEG_LEN_M) -> np.ndarray:
    """Segment boundaries (n+1 values) along a shape. Last segment is shorter, unless the remainder
    is below MIN_TAIL_M, in which case it is merged into the previous one."""
    n = n_segments(length_m, step)
    b = np.arange(n) * step
    return np.concatenate([b, [length_m]])


# --------------------------------------------------------------------------- shape store + projection
class ShapeStore:
    def __init__(self, shapes: dict[str, ShapeIndex], overlap: dict[str, bool] | None = None):
        self.shapes = shapes
        self.overlap = overlap or {}

    @classmethod
    def from_parquet(cls, path):
        df = pd.read_parquet(path, columns=["shape_id", "geometry", "self_overlap"])
        geoms = shapely.from_wkb(df["geometry"].to_numpy())
        shapes = {}
        for sid, g in zip(df["shape_id"], geoms):
            c = shapely.get_coordinates(g)
            shapes[sid] = ShapeIndex.from_lonlat(sid, c[:, 0], c[:, 1])
        return cls(shapes, dict(zip(df["shape_id"], df["self_overlap"].astype(bool))))

    def __getitem__(self, sid):
        return self.shapes[sid]


_DEFAULT_STORE: ShapeStore | None = None
DEFAULT_SHAPES_PATH = Path(__file__).resolve().parent.parent / "data" / "derived" / "shapes.parquet"


def _store(store):
    global _DEFAULT_STORE
    if store is not None:
        return store
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = ShapeStore.from_parquet(DEFAULT_SHAPES_PATH)
    return _DEFAULT_STORE


def project_points(shape_id, lon, lat, prev_m=None, *, store=None, sequential=None,
                   back_tol_m=30.0, window_m=3000.0):
    """Project points onto one shape. Returns (dist_m, offset_m) numpy arrays.

    dist_m is the distance along the shape from its first vertex; offset_m is the signed perpendicular
    distance (positive = left of the direction of travel).

    Without prev_m the nearest point on the whole polyline is used (fast, vectorised) - ambiguous where
    the shape overlaps itself. With prev_m (distance of the previous fix) or sequential=True, the
    arrays are treated as an ORDERED sequence and the result is monotonic non-decreasing; each point is
    searched near the previous one (see ShapeIndex.project_sequential).
    """
    si = _store(store)[shape_id]
    px, py = to_utm(np.atleast_1d(lon), np.atleast_1d(lat))
    if sequential is None:
        sequential = prev_m is not None
    if sequential:
        return si.project_sequential(px, py, prev_m, back_tol_m, window_m)
    return si.project_nearest(px, py)


def seg_index(store, shape_id, dist_m):
    """Segment index (consistent with segments.parquet) for distances along a shape."""
    n = store[shape_id].n_seg
    return np.minimum((np.asarray(dist_m) // SEG_LEN_M).astype(int), n - 1)


def project_dataframe(df, *, store=None, shape_col="shape_id", lon_col="lon", lat_col="lat",
                      group_cols=(), sequential="overlap"):
    """Batch projection. Adds dist_m, offset_m and seg_idx columns (NaN for unknown shapes).

    Rows are grouped by shape_col (+ group_cols, e.g. a trip/plate column); inside a group the existing
    row order is taken as time order. sequential: 'overlap' (monotonic mode only for self-overlapping
    shapes, nearest-point for the rest), True (always) or False (never). Cost: nearest mode is C-level
    (STRtree); sequential mode is a Python loop per point, so use it only where needed.
    """
    st = _store(store)
    n = len(df)
    dist = np.full(n, np.nan)
    off = np.full(n, np.nan)
    seg = np.full(n, -1, dtype=np.int64)
    keys = [shape_col, *group_cols]
    pos = pd.RangeIndex(n)
    for key, g in pd.Series(pos, index=pos).groupby([df[k].to_numpy() for k in keys], sort=False):
        sid = key[0] if isinstance(key, tuple) else key
        if sid not in st.shapes:
            continue
        rows = g.to_numpy()
        px, py = to_utm(df[lon_col].to_numpy()[rows], df[lat_col].to_numpy()[rows])
        si = st.shapes[sid]
        seq = sequential is True or (sequential == "overlap" and st.overlap.get(sid, False))
        d, o = si.project_sequential(px, py) if seq else si.project_nearest(px, py)
        dist[rows], off[rows] = d, o
        seg[rows] = np.minimum((d // SEG_LEN_M).astype(int), si.n_seg - 1)
    out = df.copy()
    out["dist_m"], out["offset_m"], out["seg_idx"] = dist, off, seg
    return out


# --------------------------------------------------------------------------- DEM
class Dem:
    """Regular lon/lat elevation grid with bilinear sampling. z[0,0] is the centre of the top-left
    pixel at (x0, y0); columns go east by res, rows go south by res. NaN = no data."""

    def __init__(self, z, x0, y0, res):
        self.z = np.asarray(z, np.float32)
        self.x0, self.y0, self.res = float(x0), float(y0), float(res)

    @classmethod
    def from_tiles(cls, paths):
        import rasterio
        tiles = []
        for p in paths:
            with rasterio.open(p) as r:
                res = r.transform.a
                tiles.append((r.read(1).astype(np.float32), r.transform.c + res / 2,
                              r.transform.f - res / 2, res, r.nodata))
        res = tiles[0][3]
        x0 = min(t[1] for t in tiles)
        y0 = max(t[2] for t in tiles)
        xe = max(t[1] + (t[0].shape[1] - 1) * res for t in tiles)
        ye = min(t[2] - (t[0].shape[0] - 1) * res for t in tiles)
        ncol = int(round((xe - x0) / res)) + 1
        nrow = int(round((y0 - ye) / res)) + 1
        z = np.full((nrow, ncol), np.nan, np.float32)
        for a, tx, ty, _, nd in tiles:
            if nd is not None:
                a = np.where(a == nd, np.nan, a)
            a = np.where(a < -1000, np.nan, a)
            c0, r0 = int(round((tx - x0) / res)), int(round((y0 - ty) / res))
            z[r0:r0 + a.shape[0], c0:c0 + a.shape[1]] = a
        return cls(z, x0, y0, res)

    def sample(self, lon, lat):
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        fx = (lon - self.x0) / self.res
        fy = (self.y0 - lat) / self.res
        nr, nc = self.z.shape
        ok = (fx >= 0) & (fx <= nc - 1) & (fy >= 0) & (fy <= nr - 1)
        fxc, fyc = np.clip(fx, 0, nc - 1), np.clip(fy, 0, nr - 1)
        c0 = np.minimum(np.floor(fxc).astype(int), nc - 2)
        r0 = np.minimum(np.floor(fyc).astype(int), nr - 2)
        wx, wy = fxc - c0, fyc - r0
        z = self.z
        v = ((1 - wx) * (1 - wy) * z[r0, c0] + wx * (1 - wy) * z[r0, c0 + 1]
             + (1 - wx) * wy * z[r0 + 1, c0] + wx * wy * z[r0 + 1, c0 + 1])
        return np.where(ok, v, np.nan)


def _running(a, win, fn):
    win = min(win, len(a) if len(a) % 2 else len(a) - 1)
    if win < 3:
        return a.copy()
    h = win // 2
    p = np.pad(a, h, mode="edge")
    return fn(np.lib.stride_tricks.sliding_window_view(p, win), axis=1)


def smooth_profile(z, median_win=MEDIAN_WIN, mean_win=MEAN_WIN):
    """Running median then running mean over an evenly spaced elevation profile. NaN gaps are filled
    by linear interpolation first (flat extrapolation at the ends); all-NaN input stays NaN."""
    z = np.asarray(z, float)
    good = np.isfinite(z)
    if good.sum() < 2:
        return np.full_like(z, np.nan)
    idx = np.arange(len(z))
    z = np.interp(idx, idx[good], z[good])
    return _running(_running(z, median_win, np.median), mean_win, np.mean)


def shape_elevation(si: ShapeIndex, dem: Dem, bounds: np.ndarray):
    """Raw and smoothed elevation at segment boundaries. Smoothing is done on a 20 m profile."""
    n = max(int(np.ceil(si.length / PROFILE_STEP_M)) + 1, 2)
    s = np.linspace(0, si.length, n)
    lon, lat = to_lonlat(*si.at(s))
    zs = smooth_profile(dem.sample(lon, lat))
    z_smooth = np.interp(bounds, s, zs)
    blon, blat = to_lonlat(*si.at(bounds))
    z_raw = dem.sample(blon, blat)
    z_smooth = np.where(np.isfinite(z_raw), z_smooth, np.nan)  # outside DEM -> no elevation
    return z_raw, z_smooth


# --------------------------------------------------------------------------- segments
def build_segments(shapes: dict[str, ShapeIndex], dem: Dem | None):
    """Cut every shape into segments. Returns a DataFrame (geometry as WKB lon/lat)."""
    import h3
    parts, all_coords, all_ids = [], [], []
    base = 0
    for sid, si in shapes.items():
        b = cut_boundaries(si.length)
        n = len(b) - 1
        sx, sy = si.at(b[:-1])
        ex, ey = si.at(b[1:])
        mx, my = si.at((b[:-1] + b[1:]) / 2)
        df = pd.DataFrame({"shape_id": sid, "seg_idx": np.arange(n, dtype=np.int32),
                           "start_m": b[:-1], "end_m": b[1:], "length_m": np.diff(b)})
        df["_sx"], df["_sy"], df["_ex"], df["_ey"], df["_mx"], df["_my"] = sx, sy, ex, ey, mx, my
        if dem is not None:
            zr, zs = shape_elevation(si, dem, b)
            df["elev_start_raw_m"], df["elev_end_raw_m"] = zr[:-1], zr[1:]
            df["elev_start_m"], df["elev_end_m"] = zs[:-1], zs[1:]
        parts.append(df)
        # polyline of each segment incl. interior vertices (UTM), flat arrays + indices
        lo = np.searchsorted(si.cum, b[:-1], side="right")
        hi = np.searchsorted(si.cum, b[1:], side="left")
        cnt = np.maximum(hi - lo, 0)
        k_i = np.repeat(np.arange(n), cnt)
        v_i = np.concatenate([np.arange(l, h) for l, h in zip(lo, hi) if h > l] or [np.array([], int)])
        ids = np.concatenate([np.arange(n), np.arange(n), k_i])
        pos = np.concatenate([b[:-1], b[1:], si.cum[v_i]])
        xs = np.concatenate([sx, ex, si.x[v_i]])
        ys = np.concatenate([sy, ey, si.y[v_i]])
        order = np.lexsort((pos, ids))
        all_ids.append(ids[order] + base)
        all_coords.append(np.column_stack([xs[order], ys[order]]))
        base += n
    seg = pd.concat(parts, ignore_index=True)
    # lon/lat of start, end, mid; bearing from geodesic azimuth start->end
    for p in ("s", "e", "m"):
        lon, lat = to_lonlat(seg[f"_{p}x"], seg[f"_{p}y"])
        name = {"s": "start", "e": "end", "m": "mid"}[p]
        seg[f"{name}_lon"], seg[f"{name}_lat"] = lon, lat
    az, _, _ = _GEOD.inv(seg.start_lon.to_numpy(), seg.start_lat.to_numpy(),
                         seg.end_lon.to_numpy(), seg.end_lat.to_numpy())
    seg["bearing_deg"] = np.mod(az, 360.0)
    seg["h3_10"] = [h3.latlng_to_cell(la, lo, 10) for la, lo in zip(seg.mid_lat, seg.mid_lon)]
    if dem is not None:
        raw = (seg.elev_end_raw_m - seg.elev_start_raw_m) / seg.length_m * 100
        sm = (seg.elev_end_m - seg.elev_start_m) / seg.length_m * 100
        seg["grade_raw_pct"] = raw
        seg["grade_unclipped_pct"] = sm
        seg["grade_pct"] = sm.clip(-GRADE_CLIP_PCT, GRADE_CLIP_PCT)
    coords = np.concatenate(all_coords)
    lon, lat = to_lonlat(coords[:, 0], coords[:, 1])
    lines = shapely.linestrings(np.column_stack([lon, lat]), indices=np.concatenate(all_ids))
    seg["geometry"] = shapely.to_wkb(lines)
    seg["_x"] = seg["_mx"]
    seg["_y"] = seg["_my"]
    return seg.drop(columns=[c for c in seg.columns if c.startswith("_") and c not in ("_x", "_y")])


# --------------------------------------------------------------------------- physical segments
def _circ_diff(a, b):
    d = np.abs(a - b) % 360.0
    return np.minimum(d, 360.0 - d)


def assign_phys_ids(seg: pd.DataFrame, shape_order, max_dist=PHYS_MAX_DIST_M,
                    max_lateral=PHYS_MAX_LATERAL_M, max_bearing=PHYS_MAX_BEARING_DEG):
    """Greedy canonical-segment matching (see docs/segmentos.md).

    Shapes are processed in shape_order. The first shape's segments become canonical physical
    segments. A segment of a later shape joins the nearest canonical segment whose midpoint is within
    max_dist, whose lateral (cross-street) distance is <= max_lateral and whose bearing differs by
    < max_bearing; otherwise it founds a new physical segment. Returns an int64 array aligned to seg.
    """
    x, y, b = seg["_x"].to_numpy(), seg["_y"].to_numpy(), seg["bearing_deg"].to_numpy()
    phys = np.full(len(seg), -1, dtype=np.int64)
    groups = seg.groupby("shape_id", sort=False).indices
    cx = np.empty(0)
    cy = np.empty(0)
    cb = np.empty(0)
    nxt = 0
    for sid in shape_order:
        rows = groups[sid]
        px, py, pb = x[rows], y[rows], b[rows]
        match = np.full(len(rows), -1, dtype=np.int64)
        if len(cx):
            tree = STRtree(shapely.points(cx, cy))
            i, j = tree.query(shapely.points(px, py), predicate="dwithin", distance=max_dist)
            if len(i):
                dx, dy = px[i] - cx[j], py[i] - cy[j]
                th = np.radians(cb[j])
                lateral = np.abs(dx * np.cos(th) - dy * np.sin(th))
                ok = (lateral <= max_lateral) & (_circ_diff(pb[i], cb[j]) < max_bearing)
                i, j, dd = i[ok], j[ok], np.hypot(dx[ok], dy[ok])
                o = np.lexsort((dd, i))
                i, j = i[o], j[o]
                if len(i):
                    first = np.r_[True, i[1:] != i[:-1]]
                    match[i[first]] = j[first]
        new = match < 0
        n_new = int(new.sum())
        match[new] = nxt + np.arange(n_new)
        cx, cy, cb = np.concatenate([cx, px[new]]), np.concatenate([cy, py[new]]), np.concatenate([cb, pb[new]])
        nxt += n_new
        phys[rows] = match
    return phys


# --------------------------------------------------------------------------- GTFS + pipeline
def _load_shapes(gtfs: Path):
    s = pd.read_csv(gtfs / "shapes.txt", dtype={"shape_id": str})
    s = s.sort_values(["shape_id", "shape_pt_sequence"], kind="stable")
    out, npts = {}, {}
    for sid, g in s.groupby("shape_id", sort=True):
        npts[sid] = len(g)
        try:
            out[sid] = ShapeIndex.from_lonlat(sid, g.shape_pt_lon.to_numpy(), g.shape_pt_lat.to_numpy())
        except ValueError as e:
            print("skipping:", e)
    return out, npts


def _shapes_table(shapes, npts, trips):
    tr = trips.groupby("shape_id").agg(route_id=("route_id", "first"), direction_id=("direction_id", "first"),
                                       n_trips=("trip_id", "size"))
    rows = []
    for sid, si in shapes.items():
        r = dict(shape_id=sid, length_m=si.length, n_points=npts[sid], **parse_shape_id(sid))
        r["self_overlap_m"] = si.self_overlap_m()
        r["self_overlap"] = r["self_overlap_m"] >= OVERLAP_MIN_LEN_M
        r["n_segments"] = si.n_seg
        lon, lat = to_lonlat(si.x, si.y)
        r["geometry"] = shapely.to_wkb(shapely.linestrings(np.column_stack([lon, lat])))
        rows.append(r)
    df = pd.DataFrame(rows).merge(tr, how="left", left_on="shape_id", right_index=True)
    df["n_trips"] = df["n_trips"].fillna(0).astype(int)
    df["has_trips"] = df["n_trips"] > 0
    df["direction_id"] = pd.to_numeric(df["direction_id"], errors="coerce").astype("Int8")
    cols = ["shape_id", "route_id", "direction_id", "length_m", "n_points", "n_trips", "route_code", "variant",
            "pre_qualifier", "direction_letter", "tail_qualifier", "id_pattern", "n_segments", "self_overlap_m",
            "self_overlap", "has_trips", "geometry"]
    return df[cols]


def _shape_stops(gtfs: Path, shapes, store):
    import duckdb
    con = duckdb.connect()
    st = (gtfs / "stop_times.txt").as_posix()
    tp = (gtfs / "trips.txt").as_posix()
    con.execute(f"""
        CREATE TEMP TABLE pat AS
        SELECT t.shape_id, t.trip_id, string_agg(s.stop_id, '>' ORDER BY s.stop_sequence) AS pattern,
               count(*) AS n_stops
        FROM read_csv('{tp}', all_varchar=true) t
        JOIN read_csv('{st}', all_varchar=true) s USING (trip_id)
        GROUP BY t.shape_id, t.trip_id""")
    # representative = most frequent stop pattern of the shape (ties: more stops), first trip_id of it
    rep = con.execute("""
        WITH c AS (SELECT shape_id, pattern, n_stops, count(*) AS n, min(trip_id) AS trip_id FROM pat
                   GROUP BY ALL),
        r AS (SELECT *, row_number() OVER (PARTITION BY shape_id ORDER BY n DESC, n_stops DESC, trip_id) AS rk
              FROM c)
        SELECT shape_id, trip_id, n AS pattern_trips FROM r WHERE rk = 1""").df()
    con.register("rep", rep)
    sq = con.execute(f"""
        SELECT r.shape_id, r.trip_id AS rep_trip_id, r.pattern_trips, s.stop_sequence::INT AS stop_seq, s.stop_id
        FROM rep r JOIN read_csv('{st}', all_varchar=true) s ON s.trip_id = r.trip_id
        ORDER BY r.shape_id, stop_seq""").df()
    stops = pd.read_csv(gtfs / "stops.txt", dtype={"stop_id": str})[["stop_id", "stop_name", "stop_lat", "stop_lon"]]
    sq = sq.merge(stops, on="stop_id", how="left")
    out = []
    for sid, g in sq.groupby("shape_id", sort=False):
        if sid not in shapes:
            continue
        si = shapes[sid]
        g = g.sort_values("stop_seq")
        ok = g.stop_lat.notna().to_numpy()
        px, py = to_utm(g.stop_lon.to_numpy(), g.stop_lat.to_numpy())
        d, o = si.project_sequential(px, py, prev_m=0.0)
        g = g.assign(stop_pos=np.arange(len(g)), dist_m=d, offset_m=o)
        g["seg_idx"] = np.minimum((np.nan_to_num(d) // SEG_LEN_M).astype(int), si.n_seg - 1)
        g["far_from_shape"] = (np.abs(o) > STOP_FAR_M) | ~ok
        out.append(g)
    res = pd.concat(out, ignore_index=True)
    return res[["shape_id", "rep_trip_id", "pattern_trips", "stop_pos", "stop_seq", "stop_id", "stop_name",
                "stop_lon", "stop_lat", "dist_m", "offset_m", "seg_idx", "far_from_shape"]]


CORRIDORS = {  # rough centre lines (lon, lat), used only for sanity reporting
    "Alameda (Est. Central - Plaza Italia)": [(-70.6786, -33.4517), (-70.6600, -33.4455), (-70.6334, -33.4372)],
    "Providencia (Plaza Italia - Tobalaba)": [(-70.6334, -33.4372), (-70.6150, -33.4310), (-70.6000, -33.4225)],
    "Vicuna Mackenna (Plaza Italia - Bellavista)": [(-70.6290, -33.4450), (-70.6230, -33.4750),
                                                    (-70.6080, -33.5000), (-70.5990, -33.5260)],
    "Apoquindo (Tobalaba - Escuela Militar - Las Condes)": [(-70.6000, -33.4225), (-70.5780, -33.4135),
                                                            (-70.5560, -33.4085)],
}


def corridor_report(seg: pd.DataFrame, shapes_df: pd.DataFrame, buffer_m=35.0):
    """Print, per corridor and direction, how many shapes cross it and how well they collapse."""
    route_of = shapes_df.set_index("shape_id")["route_code"]
    lines = []
    for name, pts in CORRIDORS.items():
        x, y = to_utm([p[0] for p in pts], [p[1] for p in pts])
        poly = shapely.linestrings(np.column_stack([x, y]))
        near = shapely.distance(poly, shapely.points(seg["_x"].to_numpy(), seg["_y"].to_numpy())) <= buffer_m
        sub = seg[near]
        if sub.empty:
            lines.append(f"{name}: no segments")
            continue
        # direction of travel relative to the centre line
        ang = np.degrees(np.arctan2(x[-1] - x[0], y[-1] - y[0])) % 360
        fwd = _circ_diff(sub.bearing_deg.to_numpy(), ang) < 90
        for lab, m in (("forward", fwd), ("reverse", ~fwd)):
            s = sub[m]
            if s.empty:
                continue
            per = s.groupby("phys_id").agg(ns=("shape_id", "nunique"))
            length_km = s.length_m.sum() / 1000
            ideal = poly.length / SEG_LEN_M
            lines.append(
                f"{name} [{lab}]: {len(s)} segments from {s.shape_id.nunique()} shapes / "
                f"{route_of.reindex(s.shape_id.unique()).nunique()} route codes -> {s.phys_id.nunique()} phys ids "
                f"(centre line is {poly.length / 1000:.2f} km = ~{ideal:.0f} ids ideal); "
                f"shapes per phys id: median {per.ns.median():.0f}, p90 {per.ns.quantile(.9):.0f}, max {per.ns.max()}")
    print("\n".join(lines))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="data/derived")
    ap.add_argument("--gtfs", default="data/external/gtfs/feed")
    ap.add_argument("--dem", default="data/external/dem")
    a = ap.parse_args(argv)
    t0 = time.time()
    gtfs, out = Path(a.gtfs), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    trips = pd.read_csv(gtfs / "trips.txt", dtype=str)
    shapes, npts = _load_shapes(gtfs)
    print(f"{len(shapes)} shapes loaded ({time.time() - t0:.0f}s)")
    sh = _shapes_table(shapes, npts, trips)
    sh.to_parquet(out / "shapes.parquet", index=False)
    print(f"shapes.parquet: {len(sh)} rows; self-overlapping: {int(sh.self_overlap.sum())} "
          f"({time.time() - t0:.0f}s)")

    dem = Dem.from_tiles(sorted(Path(a.dem).glob("*.tif")))
    seg = build_segments(shapes, dem)
    print(f"{len(seg)} segments built ({time.time() - t0:.0f}s)")

    order = sh.sort_values(["n_trips", "length_m"], ascending=False).shape_id.tolist()
    seg["phys_id"] = assign_phys_ids(seg, order)
    print(f"phys ids: {seg.phys_id.nunique()} for {len(seg)} segments ({time.time() - t0:.0f}s)")

    phys = (seg.assign(code=seg.shape_id.map(sh.set_index("shape_id").route_code))
            .groupby("phys_id").agg(mid_lon=("mid_lon", "first"), mid_lat=("mid_lat", "first"),
                                    bearing_deg=("bearing_deg", "first"), h3_10=("h3_10", "first"),
                                    n_segments=("seg_idx", "size"), n_shapes=("shape_id", "nunique"),
                                    n_routes=("code", "nunique"), length_m=("length_m", "median"),
                                    grade_pct=("grade_pct", "median"), grade_pct_min=("grade_pct", "min"),
                                    grade_pct_max=("grade_pct", "max")).reset_index())
    phys.to_parquet(out / "physical_segments.parquet", index=False)
    corridor_report(seg, sh)
    seg_out = seg.drop(columns=["_x", "_y"])
    seg_out.to_parquet(out / "segments.parquet", index=False)

    ss = _shape_stops(gtfs, shapes, None)
    ss.to_parquet(out / "shape_stops.parquet", index=False)
    print(f"shape_stops.parquet: {len(ss)} rows, far (>50 m or missing): {int(ss.far_from_shape.sum())} "
          f"({time.time() - t0:.0f}s)")
    print(f"done in {time.time() - t0:.0f}s")
    return seg, sh, phys, ss


if __name__ == "__main__":
    main()
