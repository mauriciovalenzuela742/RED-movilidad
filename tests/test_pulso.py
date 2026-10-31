import json
import time
from datetime import date, datetime

import duckdb
import pytest

from pulso import datopia as d
from pulso.testing import SCHEMA, write_day

PLACEHOLDER = {"test_user": {"email": "tu-email@ejemplo.com", "password": "TuContraseña"}}


def _cfg(path, email, password):
    path.write_text(json.dumps({"test_user": {"email": email, "password": password}}))


@pytest.fixture
def cfgs(tmp_path, monkeypatch):
    root, vendor = tmp_path / "config.json", tmp_path / "vendor.json"
    monkeypatch.setattr(d, "CONFIG_PATHS", [root, vendor])
    monkeypatch.delenv("DATOPIA_EMAIL", raising=False)
    monkeypatch.delenv("DATOPIA_PASSWORD", raising=False)
    return root, vendor


# ---------------------------------------------------------------- credentials
def test_env_wins(cfgs, monkeypatch):
    root, vendor = cfgs
    _cfg(root, "root@x.cl", "r")
    monkeypatch.setenv("DATOPIA_EMAIL", "env@x.cl")
    monkeypatch.setenv("DATOPIA_PASSWORD", "e")
    assert d.load_credentials() == ("env@x.cl", "e")


def test_root_before_vendor(cfgs):
    root, vendor = cfgs
    _cfg(root, "root@x.cl", "r")
    _cfg(vendor, "v@x.cl", "v")
    assert d.load_credentials() == ("root@x.cl", "r")


def test_vendor_fallback_and_placeholder_skipped(cfgs):
    root, vendor = cfgs
    root.write_text(json.dumps(PLACEHOLDER))
    _cfg(vendor, "v@x.cl", "v")
    assert d.load_credentials() == ("v@x.cl", "v")


def test_all_missing_lists_locations(cfgs):
    root, vendor = cfgs
    root.write_text(json.dumps(PLACEHOLDER))
    with pytest.raises(d.CredentialsError) as e:
        d.load_credentials()
    msg = str(e.value)
    assert "DATOPIA_EMAIL" in msg and str(root) in msg and str(vendor) in msg


def test_partial_env_ignored(cfgs, monkeypatch):
    monkeypatch.setenv("DATOPIA_EMAIL", "env@x.cl")
    with pytest.raises(d.CredentialsError):
        d.load_credentials()


# ---------------------------------------------------------------- paths
def test_day_path():
    assert d.day_path(date(2026, 3, 5), "s3://b/x") == "s3://b/x/year=2026/month=03/day=05/**/*.parquet"
    assert d.BASE.startswith("s3://do-datopia/categoria=transporte/pais=cl/fuente=red-movilidad/")


def test_day_paths_rollover():
    ps = d.day_paths("2025-12-30", "2026-01-02", "B")
    assert [p.split("/", 1)[1] for p in ps] == [
        "year=2025/month=12/day=30/**/*.parquet",
        "year=2025/month=12/day=31/**/*.parquet",
        "year=2026/month=01/day=01/**/*.parquet",
        "year=2026/month=01/day=02/**/*.parquet",
    ]
    with pytest.raises(ValueError):
        d.day_paths("2026-01-02", "2026-01-01")


def test_local_day_normal_winter():
    # July: Santiago is UTC-4 -> 04:00Z to 04:00Z next day
    s, e = d.local_day_bounds("2026-07-10")
    assert (s, e) == (datetime(2026, 7, 10, 4), datetime(2026, 7, 11, 4))
    ps = d.local_day_paths("2026-07-10", "B")
    assert len(ps) == 2 and "day=10" in ps[0] and "day=11" in ps[1]


def test_local_day_dst_boundaries():
    # DST starts Sun 2026-09-06 (00:00 -> 01:00): that local day is 23 h
    s, e = d.local_day_bounds("2026-09-06")
    assert s == datetime(2026, 9, 6, 4) and e == datetime(2026, 9, 7, 3)
    # DST ends Sat 2026-04-04 (24:00 -> 23:00): local day Apr 4 is 25 h
    s, e = d.local_day_bounds("2026-04-04")
    assert s == datetime(2026, 4, 4, 3) and e == datetime(2026, 4, 5, 4)
    for day in ("2026-09-06", "2026-04-04"):
        assert len(d.local_day_paths(day, "B")) == 2


def test_local_day_year_rollover():
    # Dec 31 summer (UTC-3): 03:00Z Dec 31 -> 03:00Z Jan 1
    ps = d.local_day_paths("2025-12-31", "B")
    assert "year=2025/month=12/day=31" in ps[0] and "year=2026/month=01/day=01" in ps[1]


def test_local_day_where():
    w = d.local_day_where("2026-07-10")
    assert "TIMESTAMP '2026-07-10 04:00:00'" in w and "TIMESTAMP '2026-07-11 04:00:00'" in w


# ---------------------------------------------------------------- refresh
class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self._p


class FakeHttp:
    def __init__(self, ttl=3600, bucket="do-datopia"):
        self.calls, self.ttl, self.bucket, self.n = [], ttl, bucket, 0

    def post(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/auth/login"):
            return FakeResp({"id_token": "tok"})
        self.n += 1
        return FakeResp({
            "access_key_id": f"AK{self.n}", "secret_access_key": "S'x", "session_token": "T",
            "bucket": self.bucket, "region": "us-west-2",
            "expires_at": time.time() + self.ttl, "hints": {"refresh_before_seconds": 300},
        })


def test_login_and_refresh_logic(monkeypatch):
    http = FakeHttp(ttl=3600)
    monkeypatch.setattr(d.requests, "post", http.post)
    con = duckdb.connect()
    s = d.DatopiaSession("a@b.cl", "pw")
    s.login()
    assert s.creds["access_key_id"] == "AK1" and 3500 < s.seconds_left <= 3600
    s.create_secret(con)  # also checks quote escaping
    assert s.ensure_fresh(con) is False and len(http.calls) == 2  # still fresh: no HTTP
    s.expires_at = time.time() + 299  # inside the 300 s window
    assert s.ensure_fresh(con) is True
    assert s.creds["access_key_id"] == "AK2" and len(http.calls) == 4
    assert s.ensure_fresh(con) is False


def test_custom_bucket_updates_base(monkeypatch):
    monkeypatch.setattr(d.requests, "post", FakeHttp(bucket="other").post)
    monkeypatch.setattr(d, "BASE", d.BASE)
    d.DatopiaSession("a@b.cl", "pw").login()
    assert d.BASE.startswith("s3://other/")


def test_login_http_error_is_clean(monkeypatch):
    monkeypatch.setattr(d.requests, "post", lambda *a, **k: FakeResp({"m": "no"}, 401))
    with pytest.raises(d.DatopiaError, match="401"):
        d.DatopiaSession("a@b.cl", "bad").login()


def test_expiry_formats():
    assert d._parse_expiry(1_800_000_000, 0) == 1_800_000_000
    assert d._parse_expiry(1_800_000_000_000, 0) == 1_800_000_000
    assert d._parse_expiry("1970-01-01T00:01:00Z", 0) == 60
    assert d._parse_expiry(None, 100) == 3700


# ---------------------------------------------------------------- duckdb fixture
def test_fixture_roundtrip_with_spatial(tmp_path):
    write_day(tmp_path, date(2026, 3, 14))
    write_day(tmp_path, date(2026, 3, 15))
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial")
    q = d.read_sql(d.day_path("2026-03-14", str(tmp_path).replace("\\", "/")))
    n, x, y = con.execute(
        f"SELECT count(*), min(ST_X(ST_GeomFromWKB(geometry))), max(ST_Y(ST_GeomFromWKB(geometry))) FROM {q}"
    ).fetchone()
    assert n == 3 and x == pytest.approx(-70.651) and y == pytest.approx(-33.420)
    types = dict(con.execute(f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM {q})").fetchall())
    # DuckDB auto-detects hive partitioning and adds year/month/day columns
    assert set(types) - set(SCHEMA) == {"year", "month", "day"} and set(SCHEMA) <= set(types)
    assert types["timestamp_gps_utc"] == "TIMESTAMP" and types["geometry"] == "BLOB"
    assert types["speed"] == "FLOAT" and types["direction"] == "INTEGER"


def test_local_day_filter_on_fixture(tmp_path):
    root = str(tmp_path).replace("\\", "/")
    # Santiago winter day 2026-07-10 = 04:00Z Jul 10 .. 04:00Z Jul 11
    write_day(tmp_path, date(2026, 7, 10), first_ts=datetime(2026, 7, 10, 3, 59, 0), step_seconds=60)
    write_day(tmp_path, date(2026, 7, 11), first_ts=datetime(2026, 7, 11, 3, 59, 0), step_seconds=60)
    con = duckdb.connect()
    q = d.read_sql(d.local_day_paths("2026-07-10", root))
    n = con.execute(f"SELECT count(*) FROM {q} WHERE {d.local_day_where('2026-07-10')}").fetchone()[0]
    # Jul 10 file: 03:59 (out), 04:00, 04:01 (in); Jul 11 file: 03:59 (in), 04:00, 04:01 (out)
    assert n == 3
