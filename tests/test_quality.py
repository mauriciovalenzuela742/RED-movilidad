"""Tests for pulso.quality against local fixtures with planted defects."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from pulso import quality
from pulso.testing import SCHEMA

# Santiago winter day 2026-07-10 = 04:00Z Jul 10 .. 04:00Z Jul 11 (UTC-4)
DAY = "2026-07-10"
T0 = datetime(2026, 7, 10, 12, 0, 0)  # 08:00 local


def row(plate, ts, lon, lat, speed=11.1, direction=2, way="I", consle="T201 00I",
        assigned="T201 03I", op="Op1", opnum=1, latency=2):
    return dict(plate=plate, ts=ts, tsi=ts + timedelta(seconds=latency), lon=lon, lat=lat,
                speed=speed, direction=direction, way=way, consle=consle, assigned=assigned,
                op=op, opnum=opnum)


def write_rows(root: Path, rows: list[dict]) -> None:
    """Write rows into hive partitions by the UTC date of timestamp_gps_utc (one file per day)."""
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    by_day: dict = {}
    for r in rows:
        by_day.setdefault(r["ts"].date(), []).append(r)
    for d, rs in by_day.items():
        out_dir = root / f"year={d:%Y}" / f"month={d:%m}" / f"day={d:%d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        con.execute("DROP TABLE IF EXISTS t")
        con.execute(f"CREATE TABLE t ({', '.join(f'{n} {t}' for n, t in SCHEMA.items())})")
        for r in rs:
            geom = ("NULL" if r["lon"] is None else
                    f"ST_AsWKB(ST_Point({r['lon']}, {r['lat']}))::BLOB")
            con.execute(
                "INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, " + geom + ")",
                [r["ts"], r["tsi"], r["plate"], r["way"], r["consle"], r["assigned"],
                 r["op"], r["speed"], r["direction"], r["opnum"]])
        con.execute(f"COPY t TO '{(out_dir / 'part-0.parquet').as_posix()}' (FORMAT PARQUET)")
    con.close()


def track(plate, n, step_s, dlon, dlat, lon0=-70.65, lat0=-33.45, start=T0, **kw):
    return [row(plate, start + timedelta(seconds=i * step_s), lon0 + i * dlon, lat0 + i * dlat, **kw)
            for i in range(n)]


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def val(tables, name, metric, day=DAY):
    df = tables[name]
    s = df[(df["date"] == day) & (df["metric"] == metric)]["value"]
    assert len(s) == 1, f"{name}/{metric}: {len(s)} matches"
    return float(s.iloc[0])


def run(tmp_path, rows, dates=(DAY,)):
    root = tmp_path / "data"
    write_rows(root, rows)
    out = tmp_path / "out"
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    tables = quality.profile(list(dates), base=str(root), out=out, tmp_dir=str(work), verbose=False)
    assert list(work.iterdir()) == []  # temp databases deleted
    return tables, out


# --------------------------------------------------------------------------- dirty fixture
@pytest.fixture(scope="module")
def dirty(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dirty")
    rows = []
    p1 = track("P1", 11, 30, 0.001, 0.0)
    rows += p1
    rows.append(dict(p1[2]))  # exact duplicate of P1 row 2
    # P2: teleport (+0.1 deg lon in 30 s) and return
    p2 = track("P2", 3, 30, 0.0, 0.0, lon0=-70.60, lat0=-33.40)
    p2[1]["lon"] += 0.1
    rows += p2
    # P3: normal, out of bbox + empty way, (0,0) + non-conforming route, normal; 60 s apart
    p3 = track("P3", 4, 60, 0.0, 0.0, lon0=-70.5, lat0=-33.5)
    p3[1].update(lon=-75.0, lat=-33.45, way="", tsi=p3[1]["ts"] - timedelta(seconds=5))  # latency -5 s
    p3[2].update(lon=0.0, lat=0.0, assigned="XYZ")
    rows += p3
    # P4: tiny movements, a conflicting duplicate key, one row whose way disagrees with the route letter
    p4 = track("P4", 3, 30, 0.00001, 0.0, lon0=-70.7, lat0=-33.46)
    p4[2]["way"] = "R"
    rows += p4
    conflict = dict(p4[0])
    conflict["lon"] += 0.0001
    rows.append(conflict)
    # P5: null geometry + empty way + null route; second row with 700 s latency
    p5 = track("P5", 2, 30, 0.0, 0.0, lon0=-70.8, lat0=-33.47)
    p5[0].update(lon=None, lat=None, way="", assigned=None, consle=None)
    p5[1]["tsi"] = p5[1]["ts"] + timedelta(seconds=700)
    rows += p5
    # P8: exactly at the local-day start (included)
    rows.append(row("P8", datetime(2026, 7, 10, 4, 0, 0), -70.9, -33.48))
    # neighbouring local days (excluded): last second of previous day, first second of next day
    rows.append(row("P6", datetime(2026, 7, 10, 3, 59, 59), -70.9, -33.48))
    rows.append(row("P7", datetime(2026, 7, 11, 4, 0, 0), -70.9, -33.48))
    tables, out = run(tmp_path_factory.mktemp("run"), rows)
    return tables, out, len(rows)


def test_neighbouring_days_excluded(dirty):
    tables, _, n_total = dirty
    assert val(tables, "volume", "rows") == n_total - 2 == 26
    assert val(tables, "volume", "distinct_plates") == 6  # P1..P5 + P8
    assert val(tables, "volume", "operators") == 1
    assert val(tables, "volume", "distinct_bus_route_assigned") == 2  # T201 03I, XYZ (NULL not counted)
    assert val(tables, "volume", "distinct_bus_route_consle") == 1
    assert val(tables, "volume", "rows_after_dedup") == 24


def test_nulls_and_empties(dirty):
    nulls = dirty[0]["nulls"].set_index("column_name")
    assert nulls.loc["geometry", "n_null"] == 1
    assert nulls.loc["way", "n_empty"] == 2
    assert nulls.loc["bus_route_assigned", "n_null"] == 1
    assert nulls.loc["plate", "n_null"] == 0
    assert nulls.loc["way", "share_empty"] == pytest.approx(2 / 26)


def test_duplicates(dirty):
    t = dirty[0]
    assert val(t, "duplicates", "n_exact_duplicate_rows") == 1
    assert val(t, "duplicates", "n_duplicate_keys") == 2
    assert val(t, "duplicates", "n_keys_with_conflicting_positions") == 1
    assert val(t, "duplicates", "share_duplicate_keys_conflicting") == pytest.approx(0.5)
    assert val(t, "duplicates", "n_excess_rows_by_key") == 2


def test_teleports_and_pairs(dirty):
    t = dirty[0]
    assert val(t, "speed_vs_implied", "n_teleports") == 2  # out and back
    assert val(t, "speed_vs_implied", "n_pairs") == 14  # 10 (P1) + 2 (P2) + 2 (P4)
    assert val(t, "speed_vs_implied", "share_implied_gt_120") == pytest.approx(2 / 14)


def test_spatial(dirty):
    t = dirty[0]
    assert val(t, "spatial", "n_with_coords") == 25
    assert val(t, "spatial", "n_without_coords") == 1
    assert val(t, "spatial", "n_outside_bbox") == 2  # lon -75 and (0, 0)
    assert val(t, "spatial", "n_at_0_0") == 1
    assert val(t, "spatial", "share_outside_bbox") == pytest.approx(2 / 25)
    prec = t["coord_precision"]
    lon = prec[prec["axis"] == "lon"]
    assert lon["n"].sum() == 25
    assert (lon["decimals"] >= 1).all()


def test_latency(dirty):
    t = dirty[0]
    assert val(t, "latency", "n_latency") == 26
    assert val(t, "latency", "share_negative") == pytest.approx(1 / 26)
    assert val(t, "latency", "share_gt_10min") == pytest.approx(1 / 26)
    assert val(t, "latency", "p50") == 2
    assert val(t, "latency", "min_s") == -5
    assert val(t, "latency", "max_s") == 700


def test_routes(dirty):
    t = dirty[0]
    assert val(t, "route_consistency", "share_assigned_parses") == pytest.approx(22 / 24)
    assert val(t, "route_consistency", "share_assigned_nonconforming") == pytest.approx(1 / 24)
    assert val(t, "route_consistency", "share_assigned_null") == pytest.approx(1 / 24)
    assert val(t, "route_consistency", "share_no_usable_route") == pytest.approx(2 / 24)
    assert val(t, "route_consistency", "n_both_present") == 23
    assert val(t, "route_consistency", "share_assigned_ne_consle_full_string") == pytest.approx(1.0)
    assert val(t, "route_consistency", "share_assigned_ne_consle_route_code") == pytest.approx(1 / 23)
    assert val(t, "route_consistency", "n_dir_letter_and_way_present") == 21
    assert val(t, "route_consistency", "share_dir_letter_matches_way") == pytest.approx(20 / 21)
    top = t["route_top"]
    assert top.iloc[0]["bus_route_assigned"] == "T201 03I" and top.iloc[0]["n"] == 22
    shapes = t["route_shapes"].set_index("shape")
    assert bool(shapes.loc["A999 99A", "conforms"]) and shapes.loc["A999 99A", "n"] == 22
    assert shapes.loc["AAA", "n"] == 1 and not bool(shapes.loc["AAA", "conforms"])
    assert shapes.loc["<NULL>", "n"] == 1


def test_gate_g0_and_outputs(dirty):
    tables, out, _ = dirty
    g = tables["gate_g0"].iloc[0]
    assert bool(g["median_gap_le_60s"]) is True
    assert 0 < g["share_moving_points_route_parses"] <= 1
    report = (out / "quality_report.md").read_text(encoding="utf-8")
    last = [ln for ln in report.split("\n") if ln.strip()][-2:]
    assert last[0].startswith("- Median sampling interval")
    assert last[1].startswith("- Share of moving points")
    assert "## Gate G0" in report and report.index("## Gate G0") > report.index("## Run info")
    assert "recommend" not in report.lower()
    for name in ("volume", "nulls", "duplicates", "sampling_gaps", "speed", "latency", "gate_g0",
                 "operators", "route_top", "activity_hour"):
        assert (out / f"{name}.parquet").is_file()
    assert "nan" not in report.lower().replace("n/a", "")


# --------------------------------------------------------------------------- clean fixture
@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    rows = []
    rows += track("A", 11, 30, 0.001, 0.0, direction=2, op="Op1", opnum=1)  # due east, code 2
    rows += track("B", 11, 30, 0.0, 0.001, direction=0, op="Op1", opnum=1, lon0=-70.60)  # north, code 0
    rows += track("C", 11, 30, 0.0, -0.001, direction=0, op="Op2", opnum=2, lon0=-70.55)  # south, wrong code
    rows += track("D", 3, 600, 0.0, 0.0, speed=0.0, direction=0, op="Op2", opnum=2, lon0=-70.5,
                  assigned=None, way="")
    tables, out = run(tmp_path_factory.mktemp("clean"), rows)
    return tables


def test_sampling_interval(clean):
    t = clean
    assert val(t, "sampling_gaps", "n_gaps") == 32
    assert val(t, "sampling_gaps", "gap_s_p50") == 30
    assert val(t, "sampling_gaps", "gap_s_p05") == 30
    assert val(t, "sampling_gaps", "share_le_30s") == pytest.approx(30 / 32)
    assert val(t, "sampling_gaps", "share_le_60s") == pytest.approx(30 / 32)
    assert val(t, "sampling_gaps", "share_le_15s") == 0
    assert val(t, "sampling_gaps", "share_gt_300s") == pytest.approx(2 / 32)
    assert val(t, "sampling_plate_median", "n_plates_with_gaps") == 4
    assert val(t, "sampling_plate_median", "plate_median_gap_s_p50") == 30
    assert val(t, "sampling_plate_median", "share_plates_median_le_60s") == pytest.approx(3 / 4)


def test_speed_distribution_and_implied(clean):
    t = clean
    assert val(t, "speed", "share_exactly_0") == pytest.approx(3 / 36)
    assert val(t, "speed", "max") == pytest.approx(11.1, abs=1e-4)
    assert val(t, "speed", "share_negative") == 0
    assert val(t, "speed_vs_implied", "n_pairs") == 30  # D's 600 s gaps are excluded
    assert val(t, "speed_vs_implied", "n_teleports") == 0
    east = haversine_m(-33.45, -70.65, -33.45, -70.649) / 30 * 3.6
    north = haversine_m(-33.45, -70.6, -33.449, -70.6) / 30 * 3.6
    assert val(t, "speed_vs_implied", "median_implied_kmh") == pytest.approx(
        sorted([east] * 10 + [north] * 20)[14], rel=1e-3)
    # all three moving plates report 11.1: median |reported - implied| is that of the middle element
    diffs = sorted([abs(11.1 - east)] * 10 + [abs(11.1 - north)] * 20)
    assert val(t, "speed_vs_implied", "median_abs_diff_kmh") == pytest.approx(
        (diffs[14] + diffs[15]) / 2, rel=1e-3)


def test_direction(clean):
    t = clean
    dist = t["direction_dist"].set_index("direction")["n"]
    assert dist["2"] == 11 and dist["0"] == 25
    assert val(t, "direction_agreement", "n_moved_over_30m") == 30
    # A (east, code 2) and B (north, code 0) agree; C (south) reports 0 instead of 4
    assert val(t, "direction_agreement", "share_agree_sectors_centred_on_N") == pytest.approx(20 / 30)
    assert val(t, "direction_agreement", "share_agree_sectors_starting_at_N") == pytest.approx(20 / 30)
    assert val(t, "direction_agreement", "share_within_1_sector_centred") == pytest.approx(20 / 30)
    conf = t["direction_confusion"]
    east = conf[(conf["reported_code"] == 2) & (conf["computed_code_centred"] == 2)]["n"].sum()
    assert east == 10


def test_activity_idle_operators(clean):
    t = clean
    ah = t["activity_hour"]
    assert list(ah["local_hour"]) == [8, 9, 10][:len(ah)] or set(ah["local_hour"]) <= {8, 9, 10}
    h8 = ah[ah["local_hour"] == 8].iloc[0]
    assert h8["active_plates"] == 4  # 12:00Z = 08:00 local (UTC-4)
    assert ah["rows"].sum() == 36
    assert val(t, "idle", "n_speed_0_and_no_route") == 3
    assert val(t, "idle", "share_speed_0_and_no_route") == pytest.approx(3 / 36)
    ops = t["operators"].set_index("operator_name")
    assert ops.loc["Op1", "rows"] == 22 and ops.loc["Op1", "plates"] == 2
    assert ops.loc["Op2", "rows"] == 14 and ops.loc["Op2", "plates"] == 2
    assert ops.loc["Op1", "median_gap_s"] == 30
    assert ops.loc["Op1", "share_usable_route"] == 1.0
    assert ops.loc["Op2", "share_usable_route"] == pytest.approx(11 / 14)


def test_gate_values(clean):
    g = clean["gate_g0"].iloc[0]
    assert g["median_gap_s"] == 30 and bool(g["median_gap_le_60s"])
    assert g["n_moving_points"] == 33 and g["share_moving_points_route_parses"] == 1.0


def test_cli_main_and_multiple_days(tmp_path, capsys):
    rows = track("A", 5, 30, 0.001, 0.0)
    rows += track("A", 5, 90, 0.001, 0.0, start=datetime(2026, 7, 11, 14, 0, 0))
    write_rows(tmp_path / "d", rows)
    out = tmp_path / "o"
    rc = quality.main(["--dates", "2026-07-10", "2026-07-11", "--base", str(tmp_path / "d"),
                       "--out", str(out), "--tmp-dir", str(tmp_path)])
    assert rc == 0
    g = pd.read_parquet(out / "gate_g0.parquet").set_index("date")
    assert g.loc["2026-07-10", "median_gap_s"] == 30 and g.loc["2026-07-11", "median_gap_s"] == 90
    assert bool(g.loc["2026-07-11", "median_gap_le_60s"]) is False
    assert "NO" in (out / "quality_report.md").read_text(encoding="utf-8")
    assert quality.main(["--dates", "2026-01-01", "--base", str(tmp_path / "d"),
                         "--out", str(out)]) == 1
