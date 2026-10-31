from datetime import date, timedelta

import duckdb
import pytest

from pulso import coverage
from pulso import datopia as d
from pulso.testing import write_day

START = date(2026, 1, 28)          # crosses the Jan -> Feb month boundary
SPAN_DAYS = 12                     # 2026-01-28 .. 2026-02-08
MISSING = date(2026, 2, 2)         # deliberately absent day
EMPTY = date(2026, 2, 7)           # file exists but has zero rows
TWO_PARTS = date(2026, 2, 5)       # two part files in one partition
PLATES = ["AAAA-11", "BBBB-22", "CCCC-33"]


def _rows(n):
    return [(PLATES[i % 3], -70.6 - i * 0.001, -33.4, 10.0 + i) for i in range(n)]


def build_fixture(root):
    """Write 11 existing days; return {day: {"files", "rows", "plates"}} expected values."""
    expected = {}
    for k in range(SPAN_DAYS):
        day = START + timedelta(days=k)
        if day == MISSING:
            continue
        n = 0 if day == EMPTY else 2 + k % 4
        write_day(root, day, rows=_rows(n))
        expected[day] = {"files": 1, "rows": n, "plates": min(n, 3)}
        if day == TWO_PARTS:
            write_day(root, day, rows=_rows(3), part=1)
            expected[day] = {"files": 2, "rows": n + 3, "plates": 3}
    return expected


def _read(path):
    con = duckdb.connect()
    return con.execute("SELECT * FROM read_parquet(?) ORDER BY 1", [str(path)]).fetchall()


@pytest.fixture
def fixture(tmp_path):
    base = tmp_path / "data"
    base.mkdir()
    expected = build_fixture(base)
    return base, tmp_path / "out", expected


def test_stage1_days_and_missing(fixture):
    base, out, expected = fixture
    assert coverage.main(["--base", str(base), "--out", str(out), "--stage1-only"]) == 0
    rows = _read(out / "coverage_files.parquet")
    assert len(rows) == len(expected) == 11
    assert rows[0][0] == date(2026, 1, 28) and rows[-1][0] == date(2026, 2, 8)
    assert dict((r[0], r[1]) for r in rows) == {k: v["files"] for k, v in expected.items()}
    assert coverage.missing_dates([r[0] for r in rows]) == [MISSING]
    assert not (out / "coverage_days.parquet").exists()  # stage 2 skipped


def test_full_run_per_day_counts(fixture):
    base, out, expected = fixture
    assert coverage.main(["--base", str(base), "--out", str(out)]) == 0
    rows = _read(out / "coverage_days.parquet")
    assert len(rows) == 11
    got = {r[0]: r for r in rows}
    for day, exp in expected.items():
        r = got[day]
        assert r[1] == exp["files"], day          # n_files
        assert r[2] == exp["rows"], day           # n_rows
        assert r[3] == exp["plates"], day         # distinct plates
        if exp["rows"]:
            assert r[4] == 1 and r[5] == 1, day   # one operator, one route
            assert r[6] is not None and r[6].date() == day and r[7] >= r[6]
        else:
            assert r[4] == 0 and r[5] == 0, day   # empty day: no operators/routes/timestamps
            assert r[6] is None and r[7] is None
    assert MISSING not in got
    assert (out / "coverage.html").read_text(encoding="utf-8").lower().count("plotly") > 0


def test_resume_skips_finished_days(fixture, monkeypatch):
    base, out, expected = fixture
    coverage.main(["--base", str(base), "--out", str(out)])
    first_run = _read(out / "coverage_days.parquet")

    # Simulate an interruption after the first batch (7 days) plus a torn line.
    ckpt = out / coverage.CHECKPOINT_NAME
    lines = ckpt.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(lines) == 11
    ckpt.write_text("".join(lines[:7]) + '{"day": "2026-02', encoding="utf-8")

    calls = []
    original = coverage.query_batch

    def spy(con, paths):
        calls.append(list(paths))
        return original(con, paths)

    monkeypatch.setattr(coverage, "query_batch", spy)
    assert coverage.main(["--base", str(base), "--out", str(out)]) == 0
    assert len(calls) == 1 and len(calls[0]) == 4  # only the 4 unfinished days
    assert all("day=0" in p or "day=1" in p for p in calls[0])
    assert _read(out / "coverage_days.parquet") == first_run

    calls.clear()  # a third run has nothing left to compute
    assert coverage.main(["--base", str(base), "--out", str(out)]) == 0
    assert calls == []


def test_checkpoint_from_other_base_is_not_reused(fixture, tmp_path):
    base, out, _ = fixture
    coverage.main(["--base", str(base), "--out", str(out)])
    other = tmp_path / "other"
    other.mkdir()
    build_fixture(other)  # same days, different dataset root
    rows_before = len((out / coverage.CHECKPOINT_NAME).read_text(encoding="utf-8").splitlines())
    assert coverage.main(["--base", str(other), "--out", str(out)]) == 0
    rows_after = len((out / coverage.CHECKPOINT_NAME).read_text(encoding="utf-8").splitlines())
    assert rows_after == 2 * rows_before  # every day recomputed for the new root
    assert len(_read(out / "coverage_days.parquet")) == 11


def test_days_flag_uses_most_recent_existing_days(fixture):
    base, out, expected = fixture
    assert coverage.main(["--base", str(base), "--out", str(out), "--days", "3"]) == 0
    rows = _read(out / "coverage_days.parquet")
    assert [r[0] for r in rows] == [date(2026, 2, 6), date(2026, 2, 7), date(2026, 2, 8)]


def test_local_base_never_connects(fixture, monkeypatch):
    base, out, _ = fixture

    def boom(*a, **k):
        raise AssertionError("connect() must not be called with --base")

    monkeypatch.setattr(d, "connect", boom)
    monkeypatch.setattr(d, "load_credentials", boom)
    assert coverage.main(["--base", str(base), "--out", str(out), "--stage1-only"]) == 0


def test_missing_dates_helper():
    days = [date(2026, 2, 27), date(2026, 3, 1), date(2026, 3, 4)]
    assert coverage.missing_dates(days) == [date(2026, 2, 28), date(2026, 3, 2), date(2026, 3, 3)]
    assert coverage.missing_dates([]) == []


def test_empty_base_returns_error(tmp_path, capsys):
    assert coverage.main(["--base", str(tmp_path), "--out", str(tmp_path / "o")]) == 1
    assert "No parquet files" in capsys.readouterr().out
