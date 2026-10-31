"""Phase 0 data quality profiler for the RED Movilidad bus GPS dataset.

    python -m pulso.quality --dates 2026-10-07 2026-10-10 2026-10-11 [--base PATH] [--out DIR]

Dates are America/Santiago local days. Each local day is materialised once into a
file-backed temporary DuckDB database (only the needed columns), then every metric
runs against that local copy. Outputs: one parquet per metric table plus
quality_report.md. Every number in the report is computed in the run.

Basis of each metric (stated again in the report):
  * raw rows (as received): volume, nulls, duplicates, speed distribution, spatial, latency
  * deduplicated rows (one row per plate + timestamp): sampling interval, implied speed,
    direction, route fields, activity, per-operator
"""
from __future__ import annotations

import argparse
import glob as _glob
import math
import shutil
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import pandas as pd

from . import datopia

# Region Metropolitana bounding box
LON_MIN, LON_MAX = -71.8, -69.7
LAT_MIN, LAT_MAX = -34.3, -32.9

# Documented example: "T201 03I" = route code, variant, direction I/R
ROUTE_RE = r"^(\S+) (\d{2})([IR])$"

EARTH_R = 6371008.8
MAX_GAP_FOR_SPEED = 120  # s
MIN_MOVE_M = 30.0  # m, for the direction check
TELEPORT_KMH = 120.0

RAW_COLS = ["ts", "tsi", "plate", "way", "consle", "assigned", "operator_name",
            "operator_number", "speed", "direction", "geom_null", "geom_empty", "lon", "lat"]
# raw alias -> dataset column name (for the nulls table)
NULL_COLS = [("ts", "timestamp_gps_utc"), ("tsi", "timestamp_insertion_utc"), ("plate", "plate"),
             ("way", "way"), ("consle", "bus_route_consle"), ("assigned", "bus_route_assigned"),
             ("operator_name", "operator_name"), ("operator_number", "operator_number"),
             ("speed", "speed"), ("direction", "direction")]
STRING_COLS = {"plate", "way", "consle", "assigned", "operator_name"}

SOURCE_COLS = ["timestamp_gps_utc", "timestamp_insertion_utc", "plate", "way", "bus_route_consle",
               "bus_route_assigned", "operator_name", "operator_number", "speed", "direction",
               "geometry"]


# --------------------------------------------------------------------------- SQL helpers
def _qs(col: str, probs: dict[str, float], prefix: str = "") -> str:
    return ", ".join(f"quantile_cont({col}, {p}) AS {prefix}{name}" for name, p in probs.items())


def _one(con, sql: str) -> dict:
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, cur.fetchone()))


def _num(v) -> float:
    if v is None:
        return float("nan")
    return float(v)


def _kv(day: str, d: dict, prefix: str = "") -> pd.DataFrame:
    return pd.DataFrame({"date": day, "metric": [prefix + k for k in d],
                         "value": [_num(v) for v in d.values()]})


def _frame(day: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.insert(0, "date", day)
    return df


def _haversine_m(lat1, lon1, lat2, lon2) -> str:
    return (f"(2 * {EARTH_R} * asin(sqrt(least(1.0, "
            f"pow(sin(radians(({lat2}) - ({lat1})) / 2), 2) + "
            f"cos(radians({lat1})) * cos(radians({lat2})) * "
            f"pow(sin(radians(({lon2}) - ({lon1})) / 2), 2)))))")


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


# --------------------------------------------------------------------------- materialise
def _geometry_is_blob(con, src: str) -> bool:
    rows = con.execute(f"DESCRIBE SELECT geometry FROM {src}").fetchall()
    return "BLOB" in str(rows[0][1]).upper() or "VARCHAR" in str(rows[0][1]).upper()


def materialise_day(con, session, day: str, base: str, is_local: bool) -> str:
    """Create q.raw for one Santiago local day. Returns the source expression used."""
    paths = datopia.local_day_paths(day, base)
    if is_local:
        paths = [p for p in paths if _glob.glob(p, recursive=True)]
        if not paths:
            raise FileNotFoundError(f"No parquet files under {base} cover local day {day}")
    src = datopia.read_sql(paths)
    where = datopia.local_day_where(day)
    if session is not None:
        session.ensure_fresh(con)
    blob = _geometry_is_blob(con, src)
    g = ("CASE WHEN geometry IS NULL OR octet_length(geometry) = 0 THEN NULL "
         "ELSE ST_GeomFromWKB(geometry) END") if blob else "geometry"
    if session is not None:
        session.ensure_fresh(con)
    con.execute(f"""
        CREATE TABLE q.raw AS
        SELECT ts, tsi, plate, way, consle, assigned, operator_name, operator_number, speed,
               direction, geom_null,
               (NOT geom_null AND (g IS NULL OR ST_IsEmpty(g))) AS geom_empty,
               NULLIF(CASE WHEN g IS NULL OR ST_IsEmpty(g) THEN NULL ELSE ST_X(g) END, 'NaN'::DOUBLE) AS lon,
               NULLIF(CASE WHEN g IS NULL OR ST_IsEmpty(g) THEN NULL ELSE ST_Y(g) END, 'NaN'::DOUBLE) AS lat
        FROM (
            SELECT timestamp_gps_utc AS ts, timestamp_insertion_utc AS tsi, plate, way,
                   bus_route_consle AS consle, bus_route_assigned AS assigned, operator_name,
                   operator_number, speed, direction, (geometry IS NULL) AS geom_null, {g} AS g
            FROM {src}
            WHERE {where}
        )
    """)
    return src


# --------------------------------------------------------------------------- raw-basis metrics
def raw_metrics(con, day: str, T: dict) -> None:
    def put(name, df):
        T.setdefault(name, []).append(df)

    n_rows = con.execute("SELECT count(*) FROM q.raw").fetchone()[0]

    put("volume", _kv(day, _one(con, """
        SELECT count(*) AS rows, count(DISTINCT plate) AS distinct_plates,
               count(DISTINCT operator_name) AS operators,
               count(DISTINCT operator_number) AS operator_numbers,
               count(DISTINCT assigned) AS distinct_bus_route_assigned,
               count(DISTINCT consle) AS distinct_bus_route_consle
        FROM q.raw""")))

    # nulls and empties
    parts = []
    for alias, name in NULL_COLS:
        empty = f"count_if(trim({alias}) = '')" if alias in STRING_COLS else "0"
        parts.append(f"SELECT {_sql_str(name)} AS column_name, count_if({alias} IS NULL) AS n_null, "
                     f"{empty} AS n_empty FROM q.raw")
    parts.append("SELECT 'geometry', count_if(geom_null), count_if(geom_empty) FROM q.raw")
    nulls = con.execute(" UNION ALL ".join(parts)).df()
    nulls["n_null"] = nulls["n_null"].astype("int64")
    nulls["n_empty"] = nulls["n_empty"].astype("int64")
    nulls["share_null"] = nulls["n_null"] / max(n_rows, 1)
    nulls["share_empty"] = nulls["n_empty"] / max(n_rows, 1)
    put("nulls", _frame(day, nulls))

    # duplicates
    row_expr = "row(" + ", ".join(RAW_COLS) + ")"
    dup = _one(con, f"""
        SELECT coalesce(sum(n - nd), 0) AS n_exact_duplicate_rows,
               count(*) AS n_duplicate_keys,
               coalesce(sum(n), 0) AS n_rows_in_duplicate_keys,
               coalesce(sum(n - 1), 0) AS n_excess_rows_by_key,
               coalesce(count_if(npos > 1), 0) AS n_keys_with_conflicting_positions
        FROM (SELECT count(*) AS n, count(DISTINCT {row_expr}) AS nd,
                     count(DISTINCT row(lon, lat)) AS npos
              FROM q.raw WHERE plate IS NOT NULL AND ts IS NOT NULL
              GROUP BY plate, ts HAVING count(*) > 1)""")
    dup["share_rows_exact_duplicates"] = dup["n_exact_duplicate_rows"] / max(n_rows, 1)
    dup["share_rows_excess_by_key"] = dup["n_excess_rows_by_key"] / max(n_rows, 1)
    dup["share_duplicate_keys_conflicting"] = (
        dup["n_keys_with_conflicting_positions"] / dup["n_duplicate_keys"]
        if dup["n_duplicate_keys"] else None)
    put("duplicates", _kv(day, dup))

    # speed distribution (raw)
    probs = {"p01": .01, "p05": .05, "p25": .25, "p50": .5, "p75": .75, "p95": .95, "p99": .99}
    sp = _one(con, f"""
        SELECT count(speed) AS n_speed, min(speed) AS min, {_qs('speed', probs)}, max(speed) AS max,
               avg(speed) AS mean,
               count_if(speed = 0) / nullif(count(speed), 0) AS share_exactly_0,
               count_if(speed < 0) / nullif(count(speed), 0) AS share_negative,
               count_if(speed > 90) / nullif(count(speed), 0) AS share_gt_90,
               count_if(speed > 120) / nullif(count(speed), 0) AS share_gt_120
        FROM q.raw""")
    put("speed", _kv(day, sp))

    # spatial
    inside = f"lon BETWEEN {LON_MIN} AND {LON_MAX} AND lat BETWEEN {LAT_MIN} AND {LAT_MAX}"
    sx = _one(con, f"""
        SELECT count(*) AS rows, count_if(lon IS NOT NULL AND lat IS NOT NULL) AS n_with_coords,
               count_if(lon IS NULL OR lat IS NULL) AS n_without_coords,
               count_if(lon IS NOT NULL AND lat IS NOT NULL AND NOT ({inside})) AS n_outside_bbox,
               count_if(lon = 0 AND lat = 0) AS n_at_0_0,
               min(lon) AS min_lon, max(lon) AS max_lon, min(lat) AS min_lat, max(lat) AS max_lat
        FROM q.raw""")
    sx["share_outside_bbox"] = sx["n_outside_bbox"] / sx["n_with_coords"] if sx["n_with_coords"] else None
    sx["share_at_0_0"] = sx["n_at_0_0"] / sx["n_with_coords"] if sx["n_with_coords"] else None
    sx["share_without_coords"] = sx["n_without_coords"] / max(n_rows, 1)
    sx.pop("rows")
    put("spatial", _kv(day, sx))

    def dec(col):
        s = f"CAST({col} AS VARCHAR)"
        return (f"CASE WHEN {s} LIKE '%e%' THEN 99 WHEN position('.' IN {s}) = 0 THEN 0 "
                f"ELSE length({s}) - position('.' IN {s}) END")
    prec = con.execute(f"""
        SELECT axis, least(decimals, 10) AS decimals, count(*) AS n FROM (
            SELECT 'lon' AS axis, {dec('lon')} AS decimals FROM q.raw WHERE lon IS NOT NULL
            UNION ALL
            SELECT 'lat', {dec('lat')} FROM q.raw WHERE lat IS NOT NULL)
        GROUP BY ALL ORDER BY axis, decimals""").df()
    tot = prec.groupby("axis")["n"].transform("sum")
    prec["share"] = prec["n"] / tot
    prec["note"] = prec["decimals"].map(lambda d: "10 or more (or exponent form)" if d >= 10 else "")
    put("coord_precision", _frame(day, prec))

    # latency
    lat_s = "(datediff('microsecond', ts, tsi) / 1e6)"
    lprobs = {"p01": .01, "p05": .05, "p25": .25, "p50": .5, "p75": .75, "p95": .95, "p99": .99}
    lt = _one(con, f"""
        SELECT count({lat_s}) AS n_latency, min({lat_s}) AS min_s, {_qs(lat_s, lprobs, '')},
               max({lat_s}) AS max_s,
               count_if({lat_s} < 0) / nullif(count({lat_s}), 0) AS share_negative,
               count_if({lat_s} > 600) / nullif(count({lat_s}), 0) AS share_gt_10min
        FROM q.raw""")
    put("latency", _kv(day, lt))


# --------------------------------------------------------------------------- derived table
def build_seq(con) -> None:
    """Deduplicate on (plate, ts), add previous-point lags and movement quantities."""
    con.execute(f"""
        CREATE TABLE q.clean AS
        SELECT * EXCLUDE rn FROM (
            SELECT *, row_number() OVER (PARTITION BY plate, ts
                                         ORDER BY (lon IS NULL), tsi NULLS LAST) AS rn
            FROM q.raw WHERE plate IS NOT NULL AND ts IS NOT NULL)
        WHERE rn = 1""")
    con.execute("DROP TABLE q.raw")
    con.execute(f"""
        CREATE TABLE q.stage AS
        SELECT *,
               lag(ts) OVER w AS pts, lag(lon) OVER w AS plon, lag(lat) OVER w AS plat,
               lag(valid) OVER w AS pvalid
        FROM (SELECT *,
                     coalesce(lon BETWEEN {LON_MIN} AND {LON_MAX} AND lat BETWEEN {LAT_MIN} AND {LAT_MAX}, false) AS valid,
                     coalesce(regexp_matches(assigned, '{ROUTE_RE}'), false) AS route_ok,
                     coalesce(trim(assigned) = '', true) AS route_empty
              FROM q.clean)
        WINDOW w AS (PARTITION BY plate ORDER BY ts)""")
    con.execute("DROP TABLE q.clean")
    con.execute(f"""
        CREATE TABLE q.seq AS
        SELECT * EXCLUDE (pts, plon, plat, pvalid, dist0),
               CASE WHEN pair THEN dist0 END AS dist_m,
               CASE WHEN pair THEN dist0 / dt * 3.6 END AS implied_kmh,
               CASE WHEN pair THEN (degrees(atan2(sin(radians(lon - plon)) * cos(radians(lat)),
                    cos(radians(plat)) * sin(radians(lat)) -
                    sin(radians(plat)) * cos(radians(lat)) * cos(radians(lon - plon)))) + 360) % 360 END AS bearing
        FROM (SELECT *, (valid AND coalesce(pvalid, false) AND dt > 0) AS pair,
                     CASE WHEN valid AND coalesce(pvalid, false)
                          THEN {_haversine_m('plat', 'plon', 'lat', 'lon')} END AS dist0
              FROM (SELECT *, datediff('microsecond', pts, ts) / 1e6 AS dt FROM q.stage))""")
    con.execute("DROP TABLE q.stage")


# --------------------------------------------------------------------------- deduplicated-basis metrics
def seq_metrics(con, day: str, T: dict) -> None:
    def put(name, df):
        T.setdefault(name, []).append(df)

    n_clean = con.execute("SELECT count(*) FROM q.seq").fetchone()[0]
    put("volume", _kv(day, {"rows_after_dedup": n_clean,
                            "plates_after_dedup": con.execute(
                                "SELECT count(DISTINCT plate) FROM q.seq").fetchone()[0]}))

    # ---- sampling interval
    gp = {"p05": .05, "p25": .25, "p50": .5, "p75": .75, "p95": .95, "p99": .99}
    g = _one(con, f"""
        SELECT count(dt) AS n_gaps, {_qs('dt', gp, 'gap_s_')}, max(dt) AS gap_s_max,
               count_if(dt <= 15) / nullif(count(dt), 0) AS share_le_15s,
               count_if(dt <= 30) / nullif(count(dt), 0) AS share_le_30s,
               count_if(dt <= 60) / nullif(count(dt), 0) AS share_le_60s,
               count_if(dt > 300) / nullif(count(dt), 0) AS share_gt_300s
        FROM q.seq""")
    put("sampling_gaps", _kv(day, g))
    pm = _one(con, f"""
        SELECT count(*) AS n_plates_with_gaps, {_qs('m', gp, 'plate_median_gap_s_')},
               count_if(m <= 30) / nullif(count(*), 0) AS share_plates_median_le_30s,
               count_if(m <= 60) / nullif(count(*), 0) AS share_plates_median_le_60s
        FROM (SELECT plate, median(dt) AS m FROM q.seq WHERE dt IS NOT NULL GROUP BY plate)""")
    put("sampling_plate_median", _kv(day, pm))

    # ---- implied speed vs reported
    sv = _one(con, f"""
        SELECT count(*) AS n_pairs,
               count_if(speed IS NOT NULL) AS n_pairs_with_speed,
               corr(speed, implied_kmh) AS corr_reported_vs_implied,
               corr(speed, implied_kmh) FILTER (WHERE implied_kmh <= {TELEPORT_KMH}) AS corr_excluding_teleports,
               median(abs(speed - implied_kmh)) AS median_abs_diff_kmh,
               median(speed - implied_kmh) AS median_signed_diff_kmh,
               median(implied_kmh) AS median_implied_kmh,
               count_if(implied_kmh > {TELEPORT_KMH}) AS n_teleports,
               count_if(implied_kmh > {TELEPORT_KMH}) / nullif(count(*), 0) AS share_implied_gt_120
        FROM q.seq WHERE pair AND dt <= {MAX_GAP_FOR_SPEED}""")
    put("speed_vs_implied", _kv(day, sv))

    # ---- direction
    dd = con.execute(f"""
        SELECT coalesce(CAST(d AS VARCHAR), '<NULL>') AS direction, n, n / {max(n_clean, 1)}.0 AS share
        FROM (SELECT direction AS d, count(*) AS n FROM q.seq GROUP BY direction)
        ORDER BY d NULLS LAST""").df()
    put("direction_dist", _frame(day, dd))
    mv = (f"pair AND dt <= {MAX_GAP_FOR_SPEED} AND dist_m > {MIN_MOVE_M} "
          f"AND implied_kmh <= {TELEPORT_KMH} AND direction BETWEEN 0 AND 7")
    exp_c = "CAST(floor(((bearing + 22.5) % 360) / 45) AS INTEGER)"
    exp_f = "CAST(floor(bearing / 45) AS INTEGER)"
    da = _one(con, f"""
        SELECT count(*) AS n_moved_over_30m,
               count_if(direction = {exp_c}) / nullif(count(*), 0) AS share_agree_sectors_centred_on_N,
               count_if(least((direction - {exp_c} + 8) % 8, (8 - (direction - {exp_c} + 8) % 8) % 8) <= 1)
                   / nullif(count(*), 0) AS share_within_1_sector_centred,
               count_if(direction = {exp_f}) / nullif(count(*), 0) AS share_agree_sectors_starting_at_N,
               count_if(least((direction - {exp_f} + 8) % 8, (8 - (direction - {exp_f} + 8) % 8) % 8) <= 1)
                   / nullif(count(*), 0) AS share_within_1_sector_starting
        FROM q.seq WHERE {mv}""")
    put("direction_agreement", _kv(day, da))
    dc = con.execute(f"""
        SELECT direction AS reported_code, {exp_c} AS computed_code_centred, count(*) AS n
        FROM q.seq WHERE {mv} GROUP BY ALL ORDER BY 1, 2""").df()
    put("direction_confusion", _frame(day, dc))

    # ---- route fields
    rt = con.execute(f"""
        SELECT CASE WHEN assigned IS NULL THEN '<NULL>' WHEN trim(assigned) = '' THEN '<EMPTY>'
                    ELSE assigned END AS bus_route_assigned,
               count(*) AS n, count(*) / {max(n_clean, 1)}.0 AS share
        FROM q.seq GROUP BY ALL ORDER BY n DESC, 1 LIMIT 30""").df()
    put("route_top", _frame(day, rt))
    rc = con.execute(f"""
        SELECT CASE WHEN consle IS NULL THEN '<NULL>' WHEN trim(consle) = '' THEN '<EMPTY>'
                    ELSE consle END AS bus_route_consle,
               count(*) AS n, count(*) / {max(n_clean, 1)}.0 AS share
        FROM q.seq GROUP BY ALL ORDER BY n DESC, 1 LIMIT 30""").df()
    put("route_top_consle", _frame(day, rc))

    shape = lambda c: (f"regexp_replace(regexp_replace(regexp_replace({c}, '[A-Z]', 'A', 'g'), "
                       f"'[a-z]', 'a', 'g'), '[0-9]', '9', 'g')")
    shapes = con.execute(f"""
        WITH v AS (SELECT assigned AS val, count(*) AS n FROM q.seq GROUP BY assigned),
        s AS (SELECT CASE WHEN val IS NULL THEN '<NULL>' WHEN trim(val) = '' THEN '<EMPTY>'
                          ELSE {shape('val')} END AS shape,
                     coalesce(regexp_matches(val, '{ROUTE_RE}'), false) AS conforms, val, n FROM v)
        SELECT shape, bool_or(conforms) AS conforms, sum(n) AS n, count(*) AS distinct_values,
               sum(n) / {max(n_clean, 1)}.0 AS share, arg_max(coalesce(val, ''), n) AS most_common_example
        FROM s GROUP BY shape ORDER BY n DESC, shape LIMIT 30""").df()
    put("route_shapes", _frame(day, shapes))

    rf = _one(con, f"""
        SELECT count_if(route_ok) / {max(n_clean, 1)}.0 AS share_assigned_parses,
               count_if(assigned IS NULL) / {max(n_clean, 1)}.0 AS share_assigned_null,
               count_if(assigned IS NOT NULL AND trim(assigned) = '') / {max(n_clean, 1)}.0 AS share_assigned_empty,
               count_if(NOT route_empty AND NOT route_ok) / {max(n_clean, 1)}.0 AS share_assigned_nonconforming,
               count_if(NOT route_ok) / {max(n_clean, 1)}.0 AS share_no_usable_route,
               count_if(coalesce(regexp_matches(consle, '{ROUTE_RE}'), false)) / {max(n_clean, 1)}.0 AS share_consle_parses,
               count_if(consle IS NULL OR trim(consle) = '') / {max(n_clean, 1)}.0 AS share_consle_null_or_empty,
               count_if(NOT route_empty AND consle IS NOT NULL AND trim(consle) <> '') AS n_both_present,
               count_if(NOT route_empty AND consle IS NOT NULL AND trim(consle) <> '' AND assigned <> consle)
                   / nullif(count_if(NOT route_empty AND consle IS NOT NULL AND trim(consle) <> ''), 0)
                   AS share_assigned_ne_consle_full_string,
               count_if(NOT route_empty AND consle IS NOT NULL AND trim(consle) <> ''
                        AND split_part(assigned, ' ', 1) <> split_part(consle, ' ', 1))
                   / nullif(count_if(NOT route_empty AND consle IS NOT NULL AND trim(consle) <> ''), 0)
                   AS share_assigned_ne_consle_route_code,
               count_if(NOT route_empty AND (consle IS NULL OR trim(consle) = '')) AS n_assigned_present_consle_missing,
               count_if(route_empty AND consle IS NOT NULL AND trim(consle) <> '') AS n_consle_present_assigned_missing,
               count_if(route_ok AND way IN ('I', 'R')) AS n_dir_letter_and_way_present,
               count_if(route_ok AND way IN ('I', 'R') AND right(assigned, 1) = way)
                   / nullif(count_if(route_ok AND way IN ('I', 'R')), 0) AS share_dir_letter_matches_way
        FROM q.seq""")
    put("route_consistency", _kv(day, rf))
    wx = con.execute("""
        SELECT CASE WHEN route_ok THEN right(assigned, 1) ELSE '<no parsed route>' END AS route_dir_letter,
               CASE WHEN way IS NULL THEN '<NULL>' WHEN trim(way) = '' THEN '<EMPTY>' ELSE way END AS way,
               count(*) AS n
        FROM q.seq GROUP BY ALL ORDER BY 1, 2""").df()
    put("route_way_crosstab", _frame(day, wx))

    # ---- activity
    loc = "timezone('America/Santiago', timezone('UTC', ts))"
    ah = con.execute(f"""
        SELECT hour({loc}) AS local_hour, count(*) AS rows, count(DISTINCT plate) AS active_plates
        FROM q.seq GROUP BY ALL ORDER BY 1""").df()
    put("activity_hour", _frame(day, ah))
    idle = _one(con, f"""
        SELECT count_if(speed = 0) / {max(n_clean, 1)}.0 AS share_speed_0,
               count_if(speed = 0 AND route_empty) / {max(n_clean, 1)}.0 AS share_speed_0_and_no_route,
               count_if(speed = 0 AND NOT route_ok) / {max(n_clean, 1)}.0 AS share_speed_0_and_route_not_parsing,
               count_if(speed = 0 AND route_empty) AS n_speed_0_and_no_route
        FROM q.seq""")
    put("idle", _kv(day, idle))

    # ---- per operator
    op = con.execute("""
        SELECT coalesce(operator_name, '<NULL>') AS operator_name, operator_number,
               count(*) AS rows, count(DISTINCT plate) AS plates, median(dt) AS median_gap_s,
               count_if(route_ok) / count(*) AS share_usable_route
        FROM q.seq GROUP BY operator_name, operator_number ORDER BY rows DESC, 1""").df()
    put("operators", _frame(day, op))

    # ---- inputs for Gate G0
    gm = _one(con, """
        SELECT count_if(speed > 0) AS n_moving_points_speed_gt_0,
               count_if(speed > 0 AND route_ok) AS n_moving_points_route_parses
        FROM q.seq""")
    gm["share_moving_points_route_parses"] = (
        gm["n_moving_points_route_parses"] / gm["n_moving_points_speed_gt_0"]
        if gm["n_moving_points_speed_gt_0"] else None)
    put("gate_inputs", _kv(day, gm))


# --------------------------------------------------------------------------- driver
def _dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _open_connection(base: str | None):
    """Return (con, session, base, is_local)."""
    if base:
        import duckdb
        con = duckdb.connect()
        try:
            con.execute("LOAD spatial")
        except Exception:
            con.execute("INSTALL spatial")
            con.execute("LOAD spatial")
        con.execute("SET TimeZone='UTC'")
        return con, None, str(base).replace("\\", "/"), True
    con, session = datopia.connect()
    return con, session, datopia.BASE, False  # BASE is rebound by login()


def profile(dates, base: str | None = None, out: str | Path = "outputs/phase0/quality",
            tmp_dir: str | None = None, memory_limit: str | None = None,
            verbose: bool = True) -> dict:
    dates = [str(d) for d in dates]
    for d_ in dates:
        date.fromisoformat(d_)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    con, session, base_used, is_local = _open_connection(base)
    if memory_limit:
        con.execute(f"SET memory_limit={_sql_str(memory_limit)}")
    T: dict[str, list[pd.DataFrame]] = {}
    runinfo = []
    t_all = time.time()
    for day in dates:
        work = Path(tempfile.mkdtemp(prefix="quality_", dir=tmp_dir))
        try:
            con.execute(f"SET temp_directory={_sql_str((work / 'spill').as_posix())}")
            con.execute(f"ATTACH {_sql_str((work / 'day.duckdb').as_posix())} AS q")
            t0 = time.time()
            materialise_day(con, session, day, base_used, is_local)
            t1 = time.time()
            raw_metrics(con, day, T)
            build_seq(con)
            seq_metrics(con, day, T)
            t2 = time.time()
            con.execute("CHECKPOINT q")
            size = _dir_bytes(work)
            runinfo.append({"date": day, "seconds_materialise": t1 - t0,
                            "seconds_metrics": t2 - t1, "temp_db_bytes": size})
            if verbose:
                print(f"{day}: materialise {t1 - t0:.1f}s, metrics {t2 - t1:.1f}s, "
                      f"temp db {size / 1e6:.0f} MB", flush=True)
        finally:
            try:
                con.execute("DETACH q")
            except Exception:
                pass
            shutil.rmtree(work, ignore_errors=True)
    tables = {k: pd.concat(v, ignore_index=True) for k, v in T.items()}
    tables["run_info"] = pd.DataFrame(runinfo)
    tables["gate_g0"] = gate_table(tables)
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet", index=False)
    (out / "quality_report.md").write_text(
        render_report(tables, dates, base_used, time.time() - t_all), encoding="utf-8")
    con.close()
    return tables


# --------------------------------------------------------------------------- Gate G0
def _val(df: pd.DataFrame, day: str, metric: str) -> float:
    s = df[(df["date"] == day) & (df["metric"] == metric)]["value"]
    return float(s.iloc[0]) if len(s) else float("nan")


def gate_table(tables: dict) -> pd.DataFrame:
    gaps, gi = tables["sampling_gaps"], tables["gate_inputs"]
    pm = tables["sampling_plate_median"]
    rows = []
    for day in gaps["date"].unique():
        med = _val(gaps, day, "gap_s_p50")
        mov = _val(gi, day, "n_moving_points_speed_gt_0")
        par = _val(gi, day, "n_moving_points_route_parses")
        rows.append({"date": day, "median_gap_s": med, "median_gap_le_60s": bool(med <= 60),
                     "median_of_plate_median_gaps_s": _val(pm, day, "plate_median_gap_s_p50"),
                     "n_moving_points": mov, "n_moving_points_route_parses": par,
                     "share_moving_points_route_parses": par / mov if mov else float("nan")})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- report
def _fmt(metric: str, v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    if isinstance(v, (bool,)):
        return "yes" if v else "no"
    if metric.startswith("share"):
        return f"{v * 100:.4g}%"
    v = float(v)
    if v.is_integer() or abs(v) >= 1e4:
        return f"{v:,.0f}"
    return f"{v:.7g}" if metric in ("min_lon", "max_lon", "min_lat", "max_lat") else f"{v:.4g}"


def _md(df: pd.DataFrame, fmts: dict | None = None) -> str:
    """Render a DataFrame as a markdown table; fmts maps column -> metric-name used for formatting."""
    fmts = fmts or {}
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, str):
                cells.append(v.replace("|", "\\|"))
            elif isinstance(v, (bool,)):
                cells.append("yes" if v else "no")
            else:
                cells.append(_fmt(fmts.get(c, c), v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _pivot(df: pd.DataFrame, dates: list[str]) -> str:
    p = df.pivot(index="metric", columns="date", values="value")
    order = list(dict.fromkeys(df["metric"]))
    p = p.reindex(order)[[d for d in dates if d in p.columns]]
    rows = [[m] + [_fmt(m, p.loc[m, d]) for d in p.columns] for m in p.index]
    out = pd.DataFrame(rows, columns=["metric"] + list(p.columns))
    return _md(out)


def _per_day(tables: dict, name: str, dates, drop=("date",), fmts=None, title_level="####") -> str:
    df = tables[name]
    parts = []
    for d in dates:
        sub = df[df["date"] == d].drop(columns=list(drop))
        parts.append(f"{title_level} {d}\n\n{_md(sub, fmts)}")
    return "\n\n".join(parts)


def render_report(tables: dict, dates: list[str], base: str, seconds: float) -> str:
    L: list[str] = []
    a = L.append
    a("# Data quality report: RED Movilidad bus GPS")
    a("")
    a(f"Local days profiled (America/Santiago): {', '.join(dates)}. Source: `{base}`.")
    a("")
    a("Every number below was computed in this run. Basis of each section: **raw** = rows as received "
      "for the local day; **dedup** = one row per (plate, timestamp_gps_utc), keeping a row with "
      "coordinates when keys conflict. Shares are shown as percentages; `n_` entries are row or key counts. "
      f"Route pattern used for \"parses\": regex `{ROUTE_RE}` (route code, 2-digit variant, I/R). "
      f"Bounding box: lon {LON_MIN}..{LON_MAX}, lat {LAT_MIN}..{LAT_MAX}. "
      f"Pair-based metrics (implied speed, direction) use consecutive deduplicated points of the same "
      "plate where both points are inside the bounding box.")
    a("")
    a("## 1. Volume (raw, then dedup)")
    a("")
    a(_pivot(tables["volume"], dates))
    a("")
    a("## 2. Nulls and empties (raw)")
    a("")
    a("`n_empty` counts empty or whitespace-only strings; for `geometry` it counts empty geometries "
      "(zero-length or POINT EMPTY).")
    a("")
    a(_per_day(tables, "nulls", dates))
    a("")
    a("## 3. Duplicates (raw)")
    a("")
    a("Key = (plate, timestamp_gps_utc). Conflicting = the same key appears with more than one distinct (lon, lat).")
    a("")
    a(_pivot(tables["duplicates"], dates))
    a("")
    a("## 4. Sampling interval (dedup)")
    a("")
    a("Gap = seconds between consecutive timestamps of the same plate within the local day (first row of "
      "each plate has no gap). Pooled over all plates.")
    a("")
    a(_pivot(tables["sampling_gaps"], dates))
    a("")
    a("Per-plate median gap (one median per plate, then the distribution across plates):")
    a("")
    a(_pivot(tables["sampling_plate_median"], dates))
    a("")
    a("## 5. Speed")
    a("")
    a("Reported `speed` distribution (raw, km/h):")
    a("")
    a(_pivot(tables["speed"], dates))
    a("")
    a(f"Reported speed versus speed implied by consecutive positions (dedup; pairs with gap <= "
      f"{MAX_GAP_FOR_SPEED} s; implied = haversine distance / time gap; a teleport is an implied speed > "
      f"{TELEPORT_KMH:.0f} km/h):")
    a("")
    a(_pivot(tables["speed_vs_implied"], dates))
    a("")
    a("## 6. Spatial (raw)")
    a("")
    a("`share_outside_bbox` is among rows with coordinates and includes points at (0, 0).")
    a("")
    a(_pivot(tables["spatial"], dates))
    a("")
    a("Coordinate precision (decimal places in the shortest decimal representation of the stored double):")
    a("")
    pr = tables["coord_precision"]
    for d in dates:
        sub = pr[pr["date"] == d].pivot(index="decimals", columns="axis", values="share").fillna(0).reset_index()
        sub.columns.name = None
        a(f"#### {d}\n\n" + _md(sub, {"lon": "share", "lat": "share", "decimals": "decimals"}))
        a("")
    a("(Row `10` groups 10 or more decimals, including exponent notation.)")
    a("")
    a("## 7. Latency, insertion minus GPS timestamp (raw, seconds)")
    a("")
    a(_pivot(tables["latency"], dates))
    a("")
    a("## 8. Direction (dedup)")
    a("")
    a("Value distribution of `direction`:")
    a("")
    a(_per_day(tables, "direction_dist", dates, fmts={"direction": "direction"}))
    a("")
    a(f"Agreement with the bearing computed from consecutive positions, for pairs that moved more than "
      f"{MIN_MOVE_M:.0f} m (gap <= {MAX_GAP_FOR_SPEED} s, implied speed <= {TELEPORT_KMH:.0f} km/h, code in 0..7). "
      "Two readings of \"45 degree sectors\" are reported because the convention is not documented: "
      "centred (code 0 covers 337.5..22.5 degrees) and starting at north (code 0 covers 0..45 degrees).")
    a("")
    a(_pivot(tables["direction_agreement"], dates))
    a("")
    a("Confusion counts (reported code against computed code, centred convention):")
    a("")
    a(_per_day(tables, "direction_confusion", dates, fmts={"reported_code": "x", "computed_code_centred": "x"}))
    a("")
    a("## 9. Route fields (dedup)")
    a("")
    a("Most frequent `bus_route_assigned` values:")
    a("")
    a(_per_day(tables, "route_top", dates))
    a("")
    a("Most frequent `bus_route_consle` values:")
    a("")
    a(_per_day(tables, "route_top_consle", dates))
    a("")
    a("String shapes of `bus_route_assigned` (upper case -> A, lower case -> a, digit -> 9; top 30 shapes). "
      "`conforms` = matches the documented pattern.")
    a("")
    a(_per_day(tables, "route_shapes", dates))
    a("")
    a("Consistency and usability:")
    a("")
    a(_pivot(tables["route_consistency"], dates))
    a("")
    a("Trailing I/R letter of the parsed route against the `way` column (counts):")
    a("")
    a(_per_day(tables, "route_way_crosstab", dates))
    a("")
    a("## 10. Activity (dedup)")
    a("")
    a("Active plates and rows per local hour (on a DST-change day the repeated or missing hour is merged or absent):")
    a("")
    ah = tables["activity_hour"]
    for metric in ("active_plates", "rows"):
        p = ah.pivot(index="local_hour", columns="date", values=metric).reindex(columns=dates).reset_index()
        p.columns.name = None
        a(f"{metric}:\n\n" + _md(p, {d: "n_" for d in dates} | {"local_hour": "hour"}))
        a("")
    a(_pivot(tables["idle"], dates))
    a("")
    a("Idle/depot lookalike = reported speed exactly 0 and `bus_route_assigned` null or empty.")
    a("")
    a("## 11. Per operator (dedup)")
    a("")
    a(_per_day(tables, "operators", dates, fmts={"operator_number": "operator_number"}))
    a("")
    a("## Run info")
    a("")
    ri = tables["run_info"].copy()
    ri["temp_db_MB"] = ri.pop("temp_db_bytes") / 1e6
    a(_md(ri, {"date": "d", "seconds_materialise": "s", "seconds_metrics": "s", "temp_db_MB": "MB"}))
    a("")
    a(f"Total wall time: {seconds:.1f} s.")
    a("")
    a("## Gate G0")
    a("")
    g = tables["gate_g0"]
    gi = tables["gate_inputs"]
    a(f"Moving point = deduplicated row with reported `speed` > 0. Parses = `bus_route_assigned` matches "
      f"`{ROUTE_RE}`. Median sampling interval = median of all consecutive-timestamp gaps per plate, pooled "
      f"over plates, per local day.")
    a("")
    disp = g.copy()
    disp["median_gap_le_60s"] = disp["median_gap_le_60s"].map(lambda b: "yes" if b else "no")
    a(_md(disp, {"median_gap_s": "s", "median_of_plate_median_gaps_s": "s", "n_moving_points": "n",
                 "n_moving_points_route_parses": "n"}))
    a("")
    all_ok = bool(g["median_gap_le_60s"].all())
    n_yes = int(g["median_gap_le_60s"].sum())
    mov = float(g["n_moving_points"].sum())
    par = float(g["n_moving_points_route_parses"].sum())
    a(f"- Median sampling interval <= 60 s: **{'YES' if all_ok else 'NO'}** "
      f"({n_yes} of {len(g)} days; values: "
      + ", ".join(f"{d} = {_fmt('s', v)} s" for d, v in zip(g['date'], g['median_gap_s'])) + ").")
    a(f"- Share of moving points whose route string parses, pooled over {len(g)} days: "
      f"**{_fmt('share', par / mov) if mov else 'n/a'}** ({_fmt('n', par)} of {_fmt('n', mov)}).")
    a("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Data quality profile of the RED Movilidad bus GPS data.")
    ap.add_argument("--dates", nargs="+", required=True, help="Santiago local days, YYYY-MM-DD")
    ap.add_argument("--base", help="Local dataset root (hive year=/month=/day=); no credentials needed")
    ap.add_argument("--out", default="outputs/phase0/quality", help="Output directory")
    ap.add_argument("--tmp-dir", help="Where the temporary DuckDB file is created (default: system temp)")
    ap.add_argument("--memory-limit", help="DuckDB memory limit, e.g. 8GB")
    args = ap.parse_args(argv)
    try:
        profile(args.dates, base=args.base, out=args.out, tmp_dir=args.tmp_dir,
                memory_limit=args.memory_limit)
    except (datopia.CredentialsError, datopia.DatopiaError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Report: {Path(args.out) / 'quality_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
