import datetime as dt

import polars as pl
import pytest

from mapa_red.esquema import RECORRIDO, SENTIDO, SERVICIO, SIN_RECORRIDO, dia_operativo


@pytest.mark.parametrize(("recorrido", "servicio", "sentido"), [
    ("T504 00I", "T504", "I"),
    ("T550 C0 00R", "T550", "R"),
    ("T301 06R", "T301", "R"),
    ("T13TS 00I", "T13TS", "I"),
    ("T301", "T301", "-"),            # sin sentido
    (SIN_RECORRIDO, SIN_RECORRIDO, "-"),
])
def test_servicio_y_sentido(recorrido, servicio, sentido):
    df = pl.DataFrame({"recorrido": [recorrido]}).select(servicio=SERVICIO, sentido=SENTIDO)
    assert df.row(0) == (servicio, sentido)


def test_recorrido_limpia_espacios_y_vacios():
    df = pl.DataFrame({"bus_route_assigned": [" T504 00I ", "", None]}).select(RECORRIDO)
    assert df.to_series().to_list() == ["T504 00I", SIN_RECORRIDO, SIN_RECORRIDO]


def test_dia_operativo_parte_a_las_4_hora_local():
    # 03:59 local (06:59 UTC) del 11 → día operativo del 10; 04:00 local → del 11
    ts = [dt.datetime(2025, 9, 11, 6, 59, tzinfo=dt.timezone.utc), dt.datetime(2025, 9, 11, 7, 0, tzinfo=dt.timezone.utc)]
    df = pl.DataFrame({"timestamp_gps_utc": ts}).select(dia_operativo())
    assert df.to_series().to_list() == [dt.date(2025, 9, 10), dt.date(2025, 9, 11)]
