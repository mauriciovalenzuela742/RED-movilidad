"""Smoke test: python -m pulso.ping [YYYY-MM-DD]  (UTC partition day, default two days ago)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone

from . import datopia as d


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        day = date.fromisoformat(argv[0]) if argv else (
            datetime.now(timezone.utc).date() - timedelta(days=2))
    except ValueError:
        print("Usage: python -m pulso.ping [YYYY-MM-DD]", file=sys.stderr)
        return 2
    try:
        con, session = d.connect()
        print(f"bucket={session.bucket} region={session.region} "
              f"expires_at={datetime.fromtimestamp(session.expires_at, timezone.utc):%Y-%m-%d %H:%M:%S}Z")
        session.ensure_fresh(con)
        rows, plates, tmin, tmax = con.execute(
            "SELECT count(*), count(DISTINCT plate), min(timestamp_gps_utc), "
            f"max(timestamp_gps_utc) FROM {d.read_sql(d.day_path(day, d.BASE))}"
        ).fetchone()
    except (d.CredentialsError, d.DatopiaError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"day (UTC)      : {day}")
    print(f"rows           : {rows:,}")
    print(f"distinct plates: {plates:,}")
    print(f"min timestamp  : {tmin}")
    print(f"max timestamp  : {tmax}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
