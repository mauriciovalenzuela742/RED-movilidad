"""Synthetic tests for pulso.segments (no dependency on the real GTFS/DEM files)."""
import numpy as np
import pandas as pd
import pytest
import shapely

from pulso import segments as sg

X0, Y0 = 345000.0, 6296000.0  # somewhere in Santiago, UTM 19S


def make(sid, pts):
    pts = np.asarray(pts, float)
    return sg.ShapeIndex(sid, X0 + pts[:, 0], Y0 + pts[:, 1])


def lonlat(x, y):
    return sg.to_lonlat(X0 + np.asarray(x, float), Y0 + np.asarray(y, float))


def store_of(*shapes, overlap=()):
    return sg.ShapeStore({s.shape_id: s for s in shapes}, {s: True for s in overlap})


def test_straight_length_and_segments():
    s = make("E", [(0, 0), (1000, 0)])
    assert s.length == pytest.approx(1000.0)
    b = sg.cut_boundaries(s.length)
    assert len(b) == 11 and b[0] == 0 and b[-1] == pytest.approx(1000.0)
    s2 = make("E2", [(0, 0), (1050, 0)])
    b2 = sg.cut_boundaries(s2.length)
    assert len(b2) == 12 and np.diff(b2)[-1] == pytest.approx(50.0)  # last one shorter
    s3 = make("E3", [(0, 0), (1004, 0)])  # tiny remainder is merged
    assert sg.n_segments(s3.length) == 10


def test_build_segments_bearing_and_lengths():
    east = make("E", [(0, 0), (1000, 0)])
    north = make("N", [(0, 0), (0, 450)])
    ell = make("L", [(0, 0), (500, 0), (500, 500)])
    seg = sg.build_segments({"E": east, "N": north, "L": ell}, dem=None)
    e = seg[seg.shape_id == "E"]
    assert len(e) == 10 and e.length_m.eq(100).all()
    assert np.allclose(e.bearing_deg, 90, atol=2.0)  # UTM grid vs true north differ by < 2 deg here
    n = seg[seg.shape_id == "N"]
    assert len(n) == 5 and n.length_m.iloc[-1] == pytest.approx(50.0)
    assert np.allclose(n.bearing_deg, 0, atol=2.0) or np.allclose(n.bearing_deg, 360, atol=2.0)
    l = seg[seg.shape_id == "L"]
    assert len(l) == 10 and l.length_m.sum() == pytest.approx(1000.0)
    assert np.allclose(l.bearing_deg[:5], 90, atol=2.0) and np.allclose(l.bearing_deg[5:], 0, atol=2.0)
    # the corner lies on a boundary (500 m): segment geometries are continuous polylines
    g = shapely.from_wkb(l.geometry.to_numpy())
    assert sum(x.length for x in g) > 0
    assert (l.h3_10.str.len() == 15).all()


def test_corner_inside_segment_keeps_vertex():
    ell = make("L", [(0, 0), (450, 0), (450, 300)])
    seg = sg.build_segments({"L": ell}, dem=None)
    g = shapely.from_wkb(seg.geometry.to_numpy())
    assert len(shapely.get_coordinates(g[4])) == 3  # segment 400-500 m includes the corner vertex
    assert seg.length_m.sum() == pytest.approx(750.0)


def test_projection_known_offset_and_sign():
    s = make("E", [(0, 0), (1000, 0)])
    st = store_of(s)
    lon, lat = lonlat([250, 700], [30, -12])
    d, o = sg.project_points("E", lon, lat, store=st)
    assert d == pytest.approx([250, 700], abs=0.01)
    assert o == pytest.approx([30, -12], abs=0.01)  # left of an eastbound shape is north = positive


def test_projection_on_ell_and_seg_index():
    s = make("L", [(0, 0), (500, 0), (500, 500)])
    st = store_of(s)
    lon, lat = lonlat([520, 250], [300, 0])
    d, o = sg.project_points("L", lon, lat, store=st)
    assert d == pytest.approx([800, 250], abs=0.01)
    assert o == pytest.approx([-20, 0], abs=0.01)  # east of a northbound leg is on the right
    assert list(sg.seg_index(st, "L", d)) == [8, 2]


def test_monotonic_hint_on_out_and_back():
    # out 1000 m east, 20 m north, and back west: x positions repeat -> nearest point is ambiguous
    s = make("OB", [(0, 0), (1000, 0), (1000, 20), (0, 20)])
    st = store_of(s, overlap=["OB"])
    assert s.self_overlap_m() >= 500
    xs = np.array([100, 300, 500, 700, 900, 900, 700, 500, 300, 100], float)
    ys = np.array([2, 2, 2, 2, 2, 18, 18, 18, 18, 18], float)
    lon, lat = lonlat(xs, ys)
    d, o = sg.project_points("OB", lon, lat, prev_m=0.0, store=st)
    assert np.all(np.diff(d) >= 0)
    assert d[0] == pytest.approx(100, abs=1) and d[-1] == pytest.approx(1920, abs=1)
    # a single point on the return leg, with a hint, lands on the return leg
    lon1, lat1 = lonlat([500], [18])
    d1, _ = sg.project_points("OB", lon1, lat1, prev_m=1100.0, store=st)
    assert d1[0] == pytest.approx(1020 + 500, abs=1)
    # batch helper uses sequential mode for the overlapping shape
    df = pd.DataFrame({"shape_id": "OB", "lon": lon, "lat": lat})
    out = sg.project_dataframe(df, store=st)
    assert np.all(np.diff(out.dist_m) >= 0) and out.seg_idx.iloc[-1] == 19
    # a simple shape is not flagged
    assert make("E", [(0, 0), (1000, 0)]).self_overlap_m() == 0
    # a plain crossing does not count as overlap
    cross = make("X", [(0, 0), (1000, 0), (1000, 300), (500, 300), (500, -300), (0, -300)])
    assert cross.self_overlap_m() < sg.OVERLAP_MIN_LEN_M


def test_dataframe_unknown_shape_is_nan():
    st = store_of(make("E", [(0, 0), (1000, 0)]))
    lon, lat = lonlat([10, 20], [0, 0])
    df = pd.DataFrame({"shape_id": ["E", "ZZ"], "lon": lon, "lat": lat})
    out = sg.project_dataframe(df, store=st)
    assert out.dist_m.iloc[0] == pytest.approx(10, abs=0.01) and np.isnan(out.dist_m.iloc[1])


def test_dem_bilinear_and_plane_grade():
    # plane rising 5 % towards the east, defined on a lon/lat grid
    lon0, lat0 = -70.7, -33.40
    res = 1 / 3600
    cols, rows = 400, 400
    lon_g = lon0 + np.arange(cols) * res
    m_per_deg_lon = 111320.0 * np.cos(np.radians(33.45))
    z = np.tile(1000.0 + 0.05 * (lon_g - lon0) * m_per_deg_lon, (rows, 1))
    dem = sg.Dem(z, lon0, lat0, res)
    assert dem.sample([lon0 + 10.5 * res], [lat0 - 3.2 * res])[0] == pytest.approx(
        1000.0 + 0.05 * 10.5 * res * m_per_deg_lon, abs=1e-3)
    assert np.isnan(dem.sample([lon0 - 1], [lat0])[0])  # outside the grid
    x, y = sg.to_utm([lon0 + 0.02], [lat0 - 0.05])
    s = sg.ShapeIndex("P", [x[0], x[0] + 2000], [y[0], y[0]])
    seg = sg.build_segments({"P": s}, dem)
    assert seg.grade_pct.median() == pytest.approx(5.0, abs=0.2)
    west = sg.ShapeIndex("Q", [x[0] + 2000, x[0]], [y[0], y[0]])
    segw = sg.build_segments({"Q": west}, dem)
    assert segw.grade_pct.median() == pytest.approx(-5.0, abs=0.2)
    # a segment outside the DEM has no elevation / grade
    far = sg.ShapeIndex("F", [x[0] + 40000, x[0] + 42000], [y[0], y[0]])
    assert sg.build_segments({"F": far}, dem).grade_pct.isna().all()


def test_smoothing_removes_bridge_spike_but_keeps_ramp():
    z = np.linspace(0, 40, 201)  # 20 % over... a clean ramp
    z2 = z.copy()
    z2[100:104] += 25  # 80 m bridge/underpass artefact
    sm = sg.smooth_profile(z2)
    assert np.abs(sm - z)[90:115].max() < 3.0
    assert np.abs(np.diff(sm)).max() < 2 * np.diff(z)[0] + 0.05
    assert sg.smooth_profile(np.full(10, np.nan)).tolist() == [np.nan] * 10 or np.isnan(sg.smooth_profile(np.full(10, np.nan))).all()
    filled = sg.smooth_profile([1, 1, np.nan, 1, 1, 1, 1])
    assert np.allclose(filled, 1)


def test_phys_ids_pool_same_street_only_in_same_direction():
    a = make("A", [(0, 0), (1000, 0)])
    b = make("B", [(37, 8), (1037, 8)])  # same street, shifted along-street, 8 m lateral
    c = make("C", [(1000, 0), (0, 0)])  # opposite direction
    d = make("D", [(0, 150), (1000, 150)])  # parallel street 150 m away
    shapes = {"A": a, "B": b, "C": c, "D": d}
    seg = sg.build_segments(shapes, dem=None)
    seg["phys_id"] = sg.assign_phys_ids(seg, ["A", "B", "C", "D"])
    ids = {k: set(seg[seg.shape_id == k].phys_id) for k in shapes}
    assert len(ids["A"]) == 10
    assert ids["B"] <= ids["A"] | {0} and len(ids["B"] & ids["A"]) >= 9  # pooled with A (maybe one end new)
    assert not ids["C"] & ids["A"]
    assert not ids["D"] & ids["A"]


@pytest.mark.parametrize("sid,expect", [
    ("101I", ("101", None, None, "I", None)),
    ("201eR", ("201", "e", None, "R", None)),
    ("B01I", ("B01", None, None, "I", None)),
    ("109NI", ("109", None, "N", "I", None)),
    ("E03IN", ("E03", None, None, "I", "N")),
    ("428eITNOC", ("428", "e", None, "I", "TNOC")),
    ("426IPRN", ("426", None, None, "I", "PRN")),
    ("325RPM2", ("325", None, None, "R", "PM2")),
    ("L4A-I", ("L4A", None, None, "I", None)),
    ("MTN-R", ("MTN", None, None, "R", None)),
])
def test_parse_shape_id(sid, expect):
    p = sg.parse_shape_id(sid)
    assert (p["route_code"], p["variant"], p["pre_qualifier"], p["direction_letter"], p["tail_qualifier"]) == expect
