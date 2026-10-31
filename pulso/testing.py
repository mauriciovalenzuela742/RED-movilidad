"""Offline fixtures: tiny hive-partitioned parquet with the real RED Movilidad schema."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

SCHEMA = {
    "timestamp_gps_utc": "TIMESTAMP",
    "timestamp_insertion_utc": "TIMESTAMP",
    "plate": "VARCHAR",
    "way": "VARCHAR",
    "bus_route_consle": "VARCHAR",
    "bus_route_assigned": "VARCHAR",
    "operator_name": "VARCHAR",
    "speed": "FLOAT",
    "direction": "INTEGER",
    "operator_number": "INTEGER",
    "geometry": "BLOB",  # WKB Point, EPSG:4326
}

# (plate, lon, lat, speed) around central Santiago
DEFAULT_ROWS = [
    ("AAAA-11", -70.650, -33.450, 20.0),
    ("AAAA-11", -70.651, -33.451, 0.0),
    ("BBBB-22", -70.600, -33.420, 35.5),
]


def write_day(root, day: date, rows=None, first_ts: datetime | None = None,
              step_seconds: int = 30, part: int = 0) -> Path:
    """Write root/year=YYYY/month=MM/day=DD/part-N.parquet and return the file path.

    rows: iterable of (plate, lon, lat, speed). Timestamps start at first_ts
    (default 00:00 UTC of `day`) and advance step_seconds per row. Insertion
    timestamp is GPS timestamp + 2 s.
    """
    rows = list(DEFAULT_ROWS if rows is None else rows)
    first_ts = first_ts or datetime(day.year, day.month, day.day)
    out_dir = Path(root) / f"year={day:%Y}" / f"month={day:%m}" / f"day={day:%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"part-{part}.parquet"
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    con.execute(f"CREATE TABLE t ({', '.join(f'{n} {t}' for n, t in SCHEMA.items())})")
    for i, (plate, lon, lat, speed) in enumerate(rows):
        ts = first_ts + timedelta(seconds=i * step_seconds)
        con.execute(
            "INSERT INTO t VALUES (?, ?, ?, 'I', 'T201 00I', 'T201 03I', 'Test Operator', "
            "?, 2, 1, ST_AsWKB(ST_Point(?, ?))::BLOB)",
            [ts, ts + timedelta(seconds=2), plate, speed, lon, lat],
        )
    con.execute(f"COPY t TO '{out.as_posix()}' (FORMAT PARQUET)")
    con.close()
    return out
