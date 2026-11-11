"""La descarga para el mapa deja las columnas que espera mapa/src/mapa_red, con lon/lat desde geometry."""
from datetime import date

import duckdb
import pytest

from pulso import descarga_mapa as dm
from pulso.testing import write_day


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("INSTALL spatial; LOAD spatial")
    yield c
    c.close()


def test_descarga_dia_con_geometry(tmp_path, con):
    origen, destino = tmp_path / "lake", tmp_path / "datos_local"
    dia = date(2026, 10, 7)
    write_day(origen, dia)

    filas = dm.descargar_dia(con, dia, destino, base=origen.as_posix())

    salida = dm.archivo_dia(destino, dia)
    assert salida.exists() and not salida.with_suffix(".parquet.tmp").exists()
    assert filas == 3
    # hive_partitioning=false: si no, DuckDB agrega year/month/day desde la ruta (como hace Polars sin él)
    leer = f"read_parquet('{salida.as_posix()}', hive_partitioning=false)"
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {leer}").fetchall()]
    assert cols == dm.COLUMNAS + ["lon", "lat"]
    lon, lat = con.execute(f"SELECT lon, lat FROM '{salida.as_posix()}' WHERE plate='BBBB-22'").fetchone()
    assert (lon, lat) == pytest.approx((-70.600, -33.420))


def test_archivo_dia_es_particion_hive(tmp_path):
    ruta = dm.archivo_dia(tmp_path, date(2026, 1, 5))
    assert ruta.relative_to(tmp_path).as_posix() == "year=2026/month=01/day=05/datos.parquet"
