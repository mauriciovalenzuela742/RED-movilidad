"""Coverage profile of the RED Movilidad GPS dataset: which days exist and how big each one is.

Stage 1 lists files only (glob, no data read): one row per day with its file count,
plus first/last day and the missing dates inside the range.

Stage 2 computes per-day statistics (rows, distinct plates / operators / routes,
min and max GPS timestamp) in batches of 7 days. Each finished batch is appended to a
JSONL checkpoint, so an interrupted run resumes without recomputing finished days.

Outputs (in --out, default outputs/phase0/):
    coverage_files.parquet   stage 1: one row per day with n_files
    coverage_days.parquet    stage 2: one row per day with statistics
    coverage.html            Plotly chart: rows per day and vehicles per day
    coverage_days.checkpoint.jsonl   resume checkpoint (safe to delete after a full run)

Usage:
    python -m pulso.coverage                       # remote (needs Datopia credentials)
    python -m pulso.coverage --base PATH           # local directory, no credentials
    python -m pulso.coverage --stage1-only
    python -m pulso.coverage --days 30             # most recent 30 days only
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from pulso import datopia

BATCH_DAYS = 7
CHECKPOINT_NAME = "coverage_days.checkpoint.jsonl"
MISSING_PRINT_LIMIT = 60
DEFAULT_OUT = datopia.PROJECT_ROOT / "outputs" / "phase0"

# Chart colours: first two slots of the reference palette (dataviz/references/palette.md),
# validated for light mode with scripts/validate_palette.js.
COLOR_ROWS = "#2a78d6"
COLOR_VEHICLES = "#eb6834"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"


def _sql_str(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("--days must be at least 1")
    return value


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m pulso.coverage", description=__doc__.split("\n")[0])
    p.add_argument("--base", help="local dataset root; skips Datopia login entirely")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="output directory")
    p.add_argument("--stage1-only", action="store_true", help="list files only, no data read")
    p.add_argument("--days", type=_positive_int, default=None,
                   help="stage 2 on the most recent N days only")
    return p.parse_args(argv)


# --------------------------------------------------------------------------- stage 1
def list_day_files(con, base: str) -> list[tuple[date, int]]:
    """(day, n_files) for every UTC partition day under base, sorted. Reads no data."""
    pattern = base.rstrip("/") + "/**/*.parquet"
    rows = con.execute(
        """
        WITH f AS (
            -- glob() returns backslashes on Windows; normalise so the regexes see '/'
            SELECT replace(file, chr(92), '/') AS file FROM glob(?)
        ),
        p AS (
            SELECT file,
                   regexp_extract(file, 'year=([0-9]{4})', 1) AS y,
                   regexp_extract(file, 'month=([0-9]{2})', 1) AS m,
                   regexp_extract(file, 'day=([0-9]{2})', 1) AS d
            FROM f
            WHERE regexp_matches(file, 'year=[0-9]{4}/month=[0-9]{2}/day=[0-9]{2}/')
        )
        SELECT make_date(CAST(y AS INTEGER), CAST(m AS INTEGER), CAST(d AS INTEGER)) AS day,
               count(*) AS n_files
        FROM p
        GROUP BY 1
        ORDER BY 1
        """,
        [pattern],
    ).fetchall()
    return [(r[0], int(r[1])) for r in rows]


def missing_dates(days: list[date]) -> list[date]:
    """Dates inside [first, last] that have no partition. days must be sorted and unique."""
    if not days:
        return []
    present = set(days)
    span = (days[-1] - days[0]).days + 1
    all_days = (days[0] + timedelta(i) for i in range(span))
    return [d for d in all_days if d not in present]


# --------------------------------------------------------------------------- stage 2
STATS_SQL = """
SELECT make_date(CAST("year" AS INTEGER), CAST("month" AS INTEGER), CAST("day" AS INTEGER)) AS part_day,
       count(*) AS n_rows,
       count(DISTINCT plate) AS plates,
       count(DISTINCT operator_name) AS operators,
       count(DISTINCT bus_route_assigned) AS routes,
       min(timestamp_gps_utc) AS ts_min,
       max(timestamp_gps_utc) AS ts_max
FROM {source}
GROUP BY 1
"""


def empty_stats(day: date) -> dict:
    return {"day": day.isoformat(), "n_rows": 0, "plates": 0, "operators": 0,
            "routes": 0, "ts_min": None, "ts_max": None}


def query_batch(con, paths: list[str]) -> dict[date, dict]:
    """Per-day statistics for one batch of explicit day globs. Days with no rows are absent."""
    rows = con.execute(STATS_SQL.format(source=datopia.read_sql(paths))).fetchall()
    out = {}
    for day, n_rows, plates, operators, routes, ts_min, ts_max in rows:
        out[day] = {
            "day": day.isoformat(), "n_rows": int(n_rows), "plates": int(plates),
            "operators": int(operators), "routes": int(routes),
            "ts_min": ts_min.isoformat() if ts_min else None,
            "ts_max": ts_max.isoformat() if ts_max else None,
        }
    return out


def load_checkpoint(path: Path, base: str) -> dict[str, dict]:
    """Finished days for this base, keyed by ISO date.

    Records written for another dataset root are ignored, so a fixture run can never
    make a real run skip days. A torn last line from an interrupted write is skipped.
    """
    done: dict[str, dict] = {}
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("base") == base:
            done[rec["day"]] = rec
    return done


def _ends_with_newline(path: Path) -> bool:
    with open(path, "rb") as fh:
        fh.seek(-1, os.SEEK_END)
        return fh.read(1) == b"\n"


def append_records(path: Path, records: list[dict]) -> None:
    """Append records as JSON lines and flush to disk, so a finished batch survives a crash."""
    prefix = ""
    if path.exists() and path.stat().st_size > 0 and not _ends_with_newline(path):
        prefix = "\n"  # close a torn line left by an interrupted run
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(prefix)
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def run_stage2(con, session, base: str, days: list[date], out_dir: Path, log=print):
    """Compute statistics for days not yet in the checkpoint. Returns (done, computed, resumed)."""
    ckpt = out_dir / CHECKPOINT_NAME
    done = load_checkpoint(ckpt, base)
    todo = [d for d in days if d.isoformat() not in done]
    resumed = len(days) - len(todo)
    if resumed:
        log(f"Stage 2: {resumed} day(s) already in checkpoint, skipped.")
    computed = 0
    for start in range(0, len(todo), BATCH_DAYS):
        batch = todo[start:start + BATCH_DAYS]
        if session is not None:
            session.ensure_fresh(con)  # credentials expire after one hour
        stats = query_batch(con, [datopia.day_path(d, base) for d in batch])
        records = [{**stats.get(d, empty_stats(d)), "base": base} for d in batch]
        append_records(ckpt, records)
        for rec in records:
            done[rec["day"]] = rec
        computed += len(batch)
        log(f"Stage 2: {batch[0]} .. {batch[-1]} done ({computed}/{len(todo)} new days).")
    return done, computed, resumed


# --------------------------------------------------------------------------- outputs
def _copy_parquet(con, table: str, path: Path) -> None:
    con.execute(f"COPY {table} TO {_sql_str(path.as_posix())} (FORMAT PARQUET)")


def write_files_parquet(path: Path, files: list[tuple[date, int]]) -> None:
    con = duckdb.connect()
    con.execute("CREATE TABLE coverage_files (day DATE, n_files BIGINT)")
    con.executemany("INSERT INTO coverage_files VALUES (?, ?)", files)
    _copy_parquet(con, "coverage_files", path)
    con.close()


def build_day_rows(window: list[tuple[date, int]], done: dict[str, dict]) -> list[tuple]:
    """Join stage 1 file counts with stage 2 statistics, one tuple per day."""
    rows = []
    for day, n_files in window:
        rec = done[day.isoformat()]
        rows.append((
            day, n_files, rec["n_rows"], rec["plates"], rec["operators"], rec["routes"],
            datetime.fromisoformat(rec["ts_min"]) if rec["ts_min"] else None,
            datetime.fromisoformat(rec["ts_max"]) if rec["ts_max"] else None,
        ))
    return rows


def write_days_parquet(path: Path, rows: list[tuple]) -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE coverage_days (day DATE, n_files BIGINT, n_rows BIGINT, plates BIGINT, "
        "operators BIGINT, routes BIGINT, ts_min TIMESTAMP, ts_max TIMESTAMP)"
    )
    con.executemany("INSERT INTO coverage_days VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    _copy_parquet(con, "coverage_days", path)
    con.close()


def write_chart(path: Path, rows: list[tuple]) -> None:
    """Two stacked panels sharing the x-axis (never a dual y-axis)."""
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    x = [r[0].isoformat() for r in rows]
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12,
        subplot_titles=("Rows per day", "Distinct vehicles (plate) per day"),
    )
    fig.add_trace(
        go.Bar(x=x, y=[r[2] for r in rows], name="Rows", marker_color=COLOR_ROWS,
               hovertemplate="%{x}<br>%{y:,} rows<extra></extra>"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=x, y=[r[3] for r in rows], name="Vehicles", mode="lines+markers",
                   line=dict(color=COLOR_VEHICLES, width=2),
                   marker=dict(color=COLOR_VEHICLES, size=8),
                   hovertemplate="%{x}<br>%{y:,} vehicles<extra></extra>"),
        row=2, col=1,
    )
    fig.update_layout(
        title=dict(text="RED Movilidad GPS coverage (UTC days; gaps = missing days)",
                   font=dict(color=INK_PRIMARY)),
        template="plotly_white", font=dict(color=INK_SECONDARY),
        bargap=0.2, hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="left", x=0),
    )
    fig.update_yaxes(tickformat=",", rangemode="tozero")
    fig.write_html(str(path), include_plotlyjs=True, full_html=True)


# --------------------------------------------------------------------------- summary
def _fmt_list(items: list, limit: int = MISSING_PRINT_LIMIT) -> str:
    if not items:
        return "none"
    shown = ", ".join(str(i) for i in items[:limit])
    more = len(items) - limit
    return shown + (f", ... (+{more} more)" if more > 0 else "")


def stage1_summary(files: list[tuple[date, int]], missing: list[date]) -> str:
    days = [d for d, _ in files]
    total_files = sum(n for _, n in files)
    span = (days[-1] - days[0]).days + 1
    return "\n".join([
        "== Stage 1: files ==",
        f"Parquet files: {total_files}",
        f"First day:     {days[0]}",
        f"Last day:      {days[-1]}",
        f"Days present:  {len(days)} of {span} calendar days in range",
        f"Missing dates ({len(missing)}): {_fmt_list(missing)}",
    ])


def stage2_summary(rows: list[tuple], computed: int, resumed: int) -> str:
    total_rows = sum(r[2] for r in rows)
    by_rows = sorted(rows, key=lambda r: r[2])
    plates = [r[3] for r in rows]
    zero = [r[0] for r in rows if r[2] == 0]
    multi = [r[0] for r in rows if r[1] > 1]
    lo, hi = by_rows[0], by_rows[-1]
    return "\n".join([
        "== Stage 2: per-day statistics ==",
        f"Days: {len(rows)} (computed this run: {computed}, from checkpoint: {resumed})",
        f"Total rows: {total_rows:,}",
        f"Rows per day: min {lo[2]:,} ({lo[0]}), median {int(statistics.median(r[2] for r in rows)):,}, "
        f"max {hi[2]:,} ({hi[0]})",
        f"Vehicles per day: min {min(plates)}, median {int(statistics.median(plates))}, max {max(plates)}",
        f"Days with zero rows ({len(zero)}): {_fmt_list(zero)}",
        f"Days with more than one file ({len(multi)}): {_fmt_list(multi)}",
    ])


# --------------------------------------------------------------------------- main
def main(argv=None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.base:
        con = duckdb.connect()  # local files: no Datopia login, no credentials
        session = None
        base = args.base.replace("\\", "/").rstrip("/")
    else:
        con, session = datopia.connect()
        base = datopia.BASE.rstrip("/")  # read after connect(): login may change the bucket
    print(f"Dataset root: {base}", flush=True)

    if session is not None:
        session.ensure_fresh(con)
    files = list_day_files(con, base)
    if not files:
        print("No parquet files found under the dataset root.")
        return 1
    days = [d for d, _ in files]
    missing = missing_dates(days)
    write_files_parquet(out_dir / "coverage_files.parquet", files)
    print(stage1_summary(files, missing), flush=True)
    if args.stage1_only:
        print(f"Wrote {out_dir / 'coverage_files.parquet'}")
        return 0

    window = files[-args.days:] if args.days else files
    done, computed, resumed = run_stage2(
        con, session, base, [d for d, _ in window], out_dir, log=lambda m: print(m, flush=True)
    )
    rows = build_day_rows(window, done)
    write_days_parquet(out_dir / "coverage_days.parquet", rows)
    write_chart(out_dir / "coverage.html", rows)
    print(stage2_summary(rows, computed, resumed))
    print(f"Wrote {out_dir / 'coverage_files.parquet'}, {out_dir / 'coverage_days.parquet'} "
          f"and {out_dir / 'coverage.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
