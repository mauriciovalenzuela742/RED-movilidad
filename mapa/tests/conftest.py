"""
Datos sintéticos con el mismo esquema que los parquet diarios reales y una caché construida a partir de
ellos (una vez por sesión de tests).

Día 2025-09-10 (miércoles, laboral) y 2025-09-13 (sábado). Hora de Chile en septiembre: UTC-3.
    AAAA11  T101 00I de 09:00 a 10:00 local (hacia el este), luego T101 00R de vuelta (10:20 a 11:20)
    BBBB22  T101 00I de 09:00 a 10:00, el sábado
    CCCC33  T202 00I de 09:00 a 10:00, y T303 00I solo con registros sin posición (lon / lat nulos)
    DDDD44  T404 00I con un registro fuera de la ciudad (lo descarta la caché, no el trayecto)
    EEEE55  T505 00I de punta a punta (09:00 a 10:00) y, 30 min después, una ida y vuelta con el mismo
            letrero (10:30 a 12:00, más puntos): la trayectoria típica es la de punta a punta
    FFFF66  T606 00I con un registro que salta 22 km (salto de GPS) a la mitad del viaje
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from mapa_red.cache import archivos_parquet, construir_cache
from mapa_red.config import Rutas

UTC = dt.timezone.utc
PERIODO = "2025-09"
DIA_LABORAL, DIA_SABADO = "2025-09-10", "2025-09-13"
ESQUEMA = {"timestamp_gps_utc": pl.Datetime("us", "UTC"), "plate": pl.String, "bus_route_assigned": pl.String,
           "operator_name": pl.String, "operator_number": pl.Int32, "speed": pl.Float32,
           "lon": pl.Float32, "lat": pl.Float32}


def _viaje(plate: str, ruta: str, inicio_local: dt.datetime, minutos: int, lon0: float, lon1: float,
           lat: float = -33.45, cada_s: int = 30) -> list[dict]:
    """Puntos cada `cada_s` segundos en línea recta de lon0 a lon1, a 20 km/h (detenido cada 10 puntos)."""
    n = minutos * 60 // cada_s
    t0 = (inicio_local + dt.timedelta(hours=3)).replace(tzinfo=UTC)  # local (UTC-3) → UTC
    return [{"timestamp_gps_utc": t0 + dt.timedelta(seconds=i * cada_s), "plate": plate, "bus_route_assigned": ruta,
             "operator_name": ruta.split(" ")[0], "operator_number": 1, "speed": 0.0 if i % 10 == 0 else 20.0,
             "lon": lon0 + (lon1 - lon0) * i / (n - 1), "lat": lat} for i in range(n)]


def _filas_dia(dia: str) -> list[dict]:
    d = dt.datetime.fromisoformat(dia)
    a_las = lambda h, m=0: d.replace(hour=h, minute=m)  # noqa: E731
    if dia == DIA_SABADO:
        return _viaje("BBBB22", "T101 00I", a_las(9), 60, -70.65, -70.60)
    filas = (_viaje("AAAA11", "T101 00I", a_las(9), 60, -70.65, -70.60)
             + _viaje("AAAA11", "T101 00R", a_las(10, 20), 60, -70.60, -70.65)
             + _viaje("CCCC33", "T202 00I", a_las(9), 60, -70.70, -70.68, lat=-33.50))
    sin_posicion = _viaje("CCCC33", "T303 00I", a_las(12), 10, -70.70, -70.69)
    for f in sin_posicion:
        f["lon"] = f["lat"] = None
    fuera = _viaje("DDDD44", "T404 00I", a_las(9), 10, -70.65, -70.64)
    fuera[0]["lon"] = -72.0  # fuera de la caja del Gran Santiago
    punta_a_punta = _viaje("EEEE55", "T505 00I", a_las(9), 60, -70.75, -70.70, lat=-33.40)
    ida_y_vuelta = (_viaje("EEEE55", "T505 00I", a_las(10, 30), 45, -70.75, -70.72, lat=-33.40)
                    + _viaje("EEEE55", "T505 00I", a_las(11, 15), 45, -70.72, -70.75, lat=-33.40))
    salto = _viaje("FFFF66", "T606 00I", a_las(9), 30, -70.66, -70.64, lat=-33.46)
    salto[30]["lon"] = -70.40  # 22 km al este en 30 s (sigue dentro de la caja de Santiago)
    return filas + sin_posicion + fuera + punta_a_punta + ida_y_vuelta + salto


@pytest.fixture(scope="session")
def rutas(tmp_path_factory) -> Rutas:
    base = tmp_path_factory.mktemp("datos")
    rutas = Rutas(datos_local=base / "datos_local", cache_base=base)
    for dia in (DIA_LABORAL, DIA_SABADO):
        filas = _filas_dia(dia)
        # Particiones por fecha UTC, como el lakehouse (todos los registros de prueba caen el mismo día UTC)
        y, m, d = dia.split("-")
        carpeta = rutas.datos_local / f"year={y}" / f"month={m}" / f"day={d}"
        carpeta.mkdir(parents=True)
        pl.DataFrame(filas, schema=ESQUEMA).write_parquet(carpeta / "data_0.parquet")
    archivos, periodo = archivos_parquet(rutas, PERIODO)
    construir_cache(archivos, rutas.carpeta_cache(periodo))
    return rutas
