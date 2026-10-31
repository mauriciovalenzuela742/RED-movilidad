"""Datopia Lakehouse access: login -> 1h STS credentials -> DuckDB S3 secret.

Dataset: RED Movilidad daily bus GPS positions (GeoParquet, partitioned by UTC day).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

API_URL = os.environ.get(
    "DATOPIA_API_URL", "https://ee98cnfz7e.execute-api.us-west-2.amazonaws.com/prod"
)
DEFAULT_BUCKET = "do-datopia"
DEFAULT_REGION = "us-west-2"
REFRESH_BEFORE_SECONDS = 300
SANTIAGO = ZoneInfo("America/Santiago")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATHS = [
    PROJECT_ROOT / "config.json",
    PROJECT_ROOT / "vendor" / "datopia-notebooks" / "config.json",
]
# Values shipped in vendor/datopia-notebooks/config.example.json
PLACEHOLDERS = {"tu-email@ejemplo.com", "TuContraseña", ""}

_DATASET_SUFFIX = (
    "categoria=transporte/pais=cl/fuente=red-movilidad/tipo=posicion-diaria/version=v1"
)


def _base_for(bucket: str) -> str:
    return f"s3://{bucket}/{_DATASET_SUFFIX}"


# Dataset root. Updated by DatopiaSession.login() if the response names another bucket.
BASE = _base_for(DEFAULT_BUCKET)


class CredentialsError(RuntimeError):
    """No usable Datopia credentials were found."""


class DatopiaError(RuntimeError):
    """Login or session request failed."""


# --------------------------------------------------------------------------- credentials
def _read_config(path: Path) -> tuple[str, str] | None:
    if not path.is_file():
        return None
    try:
        user = json.loads(path.read_text(encoding="utf-8-sig"))["test_user"]
        email, password = str(user["email"]).strip(), str(user["password"])
    except (ValueError, KeyError, TypeError) as exc:
        raise CredentialsError(
            f"{path} exists but is not valid. Expected "
            '{"test_user": {"email": "...", "password": "..."}}'
        ) from exc
    if email in PLACEHOLDERS or password in PLACEHOLDERS:
        return None
    return email, password


def load_credentials() -> tuple[str, str]:
    """Return (email, password): env vars, then config.json, then the vendor config.json."""
    email = os.environ.get("DATOPIA_EMAIL", "").strip()
    password = os.environ.get("DATOPIA_PASSWORD", "")
    if email and password and email not in PLACEHOLDERS and password not in PLACEHOLDERS:
        return email, password
    for path in CONFIG_PATHS:
        found = _read_config(path)
        if found:
            return found
    raise CredentialsError(
        "No Datopia credentials found. Provide them in one of these places "
        "(checked in this order):\n"
        "  1. environment variables DATOPIA_EMAIL and DATOPIA_PASSWORD\n"
        f"  2. {CONFIG_PATHS[0]}\n"
        f"  3. {CONFIG_PATHS[1]}\n"
        'Files use the shape {"test_user": {"email": "you@example.com", "password": "..."}}; '
        "placeholder values from config.example.json are ignored."
    )


# --------------------------------------------------------------------------- session
def _parse_expiry(value, now: float) -> float:
    """expires_at may be epoch seconds/ms or an ISO string; default to now + 1 h."""
    try:
        if value is None:
            raise ValueError
        if isinstance(value, (int, float)) or str(value).replace(".", "", 1).isdigit():
            v = float(value)
            return v / 1000 if v > 1e11 else v
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return now + 3600


def _sql_str(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


class DatopiaSession:
    """Holds STS credentials and keeps a DuckDB S3 secret fresh."""

    def __init__(self, email: str | None = None, password: str | None = None,
                 api_url: str = API_URL, timeout: float = 30):
        if email is None or password is None:
            email, password = load_credentials()
        self.email, self._password = email, password
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.creds: dict = {}
        self.bucket = DEFAULT_BUCKET
        self.region = DEFAULT_REGION
        self.expires_at = 0.0  # epoch seconds
        self.refresh_before = REFRESH_BEFORE_SECONDS
        self._lock = threading.RLock()

    # -- HTTP
    def _post(self, path: str, **kwargs) -> dict:
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = requests.post(f"{self.api_url}{path}", timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last = exc
            else:
                if resp.status_code < 400:
                    return resp.json()
                if resp.status_code < 500:  # bad credentials etc.: do not retry
                    raise DatopiaError(
                        f"{path} failed with HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                last = DatopiaError(f"{path} failed with HTTP {resp.status_code}")
            time.sleep(0.5 * (attempt + 1))
        raise DatopiaError(f"{path} unreachable after 3 attempts: {last}")

    def login(self) -> None:
        """Log in and fetch fresh S3 credentials."""
        global BASE
        with self._lock:
            tok = self._post("/auth/login", json={"email": self.email, "password": self._password})
            id_token = tok.get("id_token")
            if not id_token:
                raise DatopiaError("Login response did not include id_token.")
            creds = self._post("/auth/session/s3", headers={"Authorization": f"Bearer {id_token}"})
            for key in ("access_key_id", "secret_access_key", "session_token"):
                if key not in creds:
                    raise DatopiaError(f"S3 session response is missing '{key}'.")
            self.creds = creds
            self.bucket = creds.get("bucket") or DEFAULT_BUCKET
            self.region = creds.get("region") or DEFAULT_REGION
            self.refresh_before = int(
                (creds.get("hints") or {}).get("refresh_before_seconds", REFRESH_BEFORE_SECONDS)
            )
            self.expires_at = _parse_expiry(creds.get("expires_at"), time.time())
            BASE = _base_for(self.bucket)

    @property
    def seconds_left(self) -> float:
        return self.expires_at - time.time()

    def needs_refresh(self) -> bool:
        return self.seconds_left < self.refresh_before

    def create_secret(self, con) -> None:
        c = self.creds
        con.execute(
            "CREATE OR REPLACE SECRET s3 (TYPE S3, "
            f"KEY_ID {_sql_str(c['access_key_id'])}, "
            f"SECRET {_sql_str(c['secret_access_key'])}, "
            f"SESSION_TOKEN {_sql_str(c['session_token'])}, "
            f"REGION {_sql_str(self.region)})"
        )

    def ensure_fresh(self, con) -> bool:
        """Re-login and recreate the secret if < 300 s remain. Cheap; call before every query.

        Returns True if credentials were refreshed. Thread-safe.
        """
        with self._lock:
            if not self.needs_refresh():
                return False
            self.login()
            self.create_secret(con)
            return True


def connect(session: DatopiaSession | None = None):
    """Return (duckdb connection, DatopiaSession) ready to query the lakehouse."""
    import duckdb

    session = session or DatopiaSession()
    session.login()
    con = duckdb.connect()
    for ext in ("httpfs", "spatial"):
        con.execute(f"INSTALL {ext}")
        con.execute(f"LOAD {ext}")
    con.execute("SET TimeZone='UTC'")
    session.create_secret(con)
    return con, session


# --------------------------------------------------------------------------- paths
def _as_date(d) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d))


def day_path(d, base: str | None = None) -> str:
    """Glob for one UTC partition day."""
    d = _as_date(d)
    return f"{base or BASE}/year={d:%Y}/month={d:%m}/day={d:%d}/**/*.parquet"


def day_paths(start, end, base: str | None = None) -> list[str]:
    """Globs for every UTC partition day from start to end, inclusive."""
    s, e = _as_date(start), _as_date(end)
    if e < s:
        raise ValueError(f"end {e} is before start {s}")
    return [day_path(s + timedelta(days=i), base) for i in range((e - s).days + 1)]


def local_day_bounds(local_date) -> tuple[datetime, datetime]:
    """[start, end) of an America/Santiago calendar day as naive UTC datetimes.

    Matches the naive-UTC TIMESTAMP columns. DST days are 23 or 25 hours long.
    """
    d = _as_date(local_date)
    nxt = d + timedelta(days=1)
    start = datetime(d.year, d.month, d.day, tzinfo=SANTIAGO)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=SANTIAGO)
    utc = timezone.utc
    return start.astimezone(utc).replace(tzinfo=None), end.astimezone(utc).replace(tzinfo=None)


def local_day_paths(local_date, base: str | None = None) -> list[str]:
    """UTC partition globs covering one Santiago calendar day (always two partitions).

    ALWAYS combine with local_day_where(): the partitions also hold rows from the
    neighbouring local days.
    """
    start, end = local_day_bounds(local_date)
    last = (end - timedelta(microseconds=1)).date()
    return day_paths(start.date(), last, base)


def local_day_where(local_date, column: str = "timestamp_gps_utc") -> str:
    """SQL predicate selecting exactly one Santiago calendar day (half-open, UTC bounds)."""
    start, end = local_day_bounds(local_date)
    return (
        f"({column} >= TIMESTAMP '{start:%Y-%m-%d %H:%M:%S}' "
        f"AND {column} < TIMESTAMP '{end:%Y-%m-%d %H:%M:%S}')"
    )


def read_sql(paths) -> str:
    """read_parquet(...) expression over one glob or a list of globs."""
    if isinstance(paths, str):
        paths = [paths]
    lst = ", ".join(_sql_str(p) for p in paths)
    return f"read_parquet([{lst}], union_by_name=true)"
