import datetime as dt

import polars as pl
import pytest

from mapa_red.esquema import RECORRIDO, SENTIDO, SERVICIO, SIN_RECORRIDO, dia_operativo, viaje


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


def test_viaje_se_corta_por_recorrido_pausa_o_salto_y_parte_de_0_en_cada_bus():
    t0 = dt.datetime(2025, 9, 10, 12, tzinfo=dt.timezone.utc)
    s = [0, 30, 60, 60 + 16 * 60, 90 + 16 * 60, 120 + 16 * 60, 150 + 16 * 60, 0, 30]
    df = pl.DataFrame({
        "patente": ["A"] * 7 + ["B"] * 2,
        "ts": [t0 + dt.timedelta(seconds=x) for x in s],
        "recorrido": ["R1"] * 5 + ["R2"] * 2 + ["R1"] * 2,
        # El bus A salta ~11 km en 30 s en su tercer punto (salto de GPS), después pasa 16 minutos sin señal y al
        # final cambia de recorrido. El bus B vuelve a partir de 0.
        "lon": [-70.60, -70.601, -70.48, -70.602, -70.603, -70.604, -70.605, -70.60, -70.601],
        "lat": [-33.45] * 9,
    })
    assert df.select(viaje()).to_series().to_list() == [0, 0, 1, 2, 2, 3, 3, 0, 0]
