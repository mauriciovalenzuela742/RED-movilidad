"""Temporary STS credentials given by hand (no email/password)."""
import json
import time

import pytest

from pulso import datopia as d


@pytest.fixture
def no_login(monkeypatch, tmp_path):
    for k in ("DATOPIA_EMAIL", "DATOPIA_PASSWORD", "AWS_ACCESS_KEY_ID",
              "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(d, "CONFIG_PATHS", [tmp_path / "config.json", tmp_path / "v.json"])
    sts = tmp_path / "sts.json"
    monkeypatch.setattr(d, "STS_PATHS", [sts])
    return sts


def test_sts_file_from_session_endpoint(no_login):
    no_login.write_text(json.dumps({
        "access_key_id": "AK", "secret_access_key": "SK", "session_token": "ST",
        "bucket": "do-datopia", "region": "us-west-2",
        "expires_at": time.time() + 3600,
    }))
    s = d.DatopiaSession()
    assert s.static
    s.login()
    assert s.creds["access_key_id"] == "AK" and not s.needs_refresh()


def test_sts_aws_names_and_env(no_login, monkeypatch):
    no_login.write_text(json.dumps({"Credentials": {
        "AccessKeyId": "AK", "SecretAccessKey": "SK", "SessionToken": "ST"}}))
    s = d.DatopiaSession(); s.login()
    assert s.creds["session_token"] == "ST"
    no_login.unlink()
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "A2")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "S2")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "T2")
    s.login()
    assert s.creds["access_key_id"] == "A2"


def test_sts_expired_is_clear_error(no_login):
    no_login.write_text(json.dumps({
        "access_key_id": "AK", "secret_access_key": "SK", "session_token": "ST",
        "expires_at": "2020-01-01T00:00:00Z"}))
    with pytest.raises(d.DatopiaError, match="expired"):
        d.DatopiaSession().login()


def test_no_credentials_mentions_sts(no_login):
    with pytest.raises(d.CredentialsError, match="sts.json"):
        d.DatopiaSession()
