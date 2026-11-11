import json

import polars as pl

from mapa_red.cache import VERSION

from .conftest import PERIODO


def test_meta(rutas):
    meta = json.loads((rutas.carpeta_cache(PERIODO) / "meta.json").read_text(encoding="utf-8"))
    assert meta["version"] == VERSION
    assert meta["dias"] == {"0": 1, "1": 1}  # un día laboral y un sábado


def test_agg_sin_columnas_que_no_usa_la_app(rutas):
    carpeta = rutas.carpeta_cache(PERIODO)
    columnas = pl.scan_parquet(carpeta / "agg" / "*.parquet").collect_schema().names()
    assert "chofer" not in columnas
    assert not (carpeta / "op_bus.parquet").exists()


def test_ranking_patente_guarda_el_grupo_de_cada_bus(rutas):
    carpeta = rutas.carpeta_cache(PERIODO)
    grupos = pl.read_parquet(carpeta / "ranking_patente.parquet", columns=["patente", "grupo"])
    for patente, g in grupos.iter_rows():  # cada bus está solo en el archivo de su grupo
        assert pl.read_parquet(carpeta / "agg" / f"grupo={g:02d}.parquet")["patente"].to_list().count(patente) > 0


def test_ranking_por_recorrido(rutas):
    r = pl.read_parquet(rutas.carpeta_cache(PERIODO) / "ranking_recorrido.parquet")
    por_id = {f["recorrido"]: f for f in r.iter_rows(named=True)}
    assert por_id["T101 00I"]["buses"] == 2  # AAAA11 el miércoles y BBBB22 el sábado
    assert por_id["T101 00I"]["n_total"] == 240
    assert "T303 00I" not in por_id  # sin posición: no hay celda donde ponerlo
    assert por_id["T404 00I"]["n_total"] == 19  # el registro fuera de la ciudad se descarta


def test_dias_bus_solo_cuenta_registros_con_posicion(rutas):
    dias = pl.read_parquet(rutas.carpeta_cache(PERIODO) / "dias_bus.parquet")
    assert dias.filter(pl.col("recorrido") == "T303 00I").is_empty()
    cccc = dias.filter(pl.col("patente") == "CCCC33")
    assert cccc["recorrido"].to_list() == ["T202 00I"]


def test_trayectos_un_archivo_por_dia_ordenado_por_recorrido(rutas):
    carpeta = rutas.carpeta_cache(PERIODO) / "trayectos"
    assert sorted(p.name for p in carpeta.iterdir()) == ["dia=2025-09-10.parquet", "dia=2025-09-13.parquet"]
    df = pl.scan_parquet(carpeta / "dia=2025-09-10.parquet").collect()
    assert df.columns == ["servicio", "sentido", "recorrido", "patente", "ts", "lon", "lat", "speed", "viaje"]
    # Ordenado por recorrido: así, filtrar por servicio o recorrido se salta el resto del archivo
    assert df.equals(df.sort("recorrido", "patente", "ts"))
    assert "T303 00I" not in df["recorrido"]  # solo los registros con posición, igual que dias_bus
    assert -72.0 in df["lon"]  # el punto fuera de la ciudad se queda (las celdas sí lo descartan)


def test_tipicas_una_por_recorrido(rutas):
    tipicas = pl.scan_parquet(rutas.carpeta_cache(PERIODO) / "tipicas.parquet").collect()
    assert tipicas["recorrido"].is_unique().all()
    # Todos los recorridos tienen la suya, menos T303 00I, que no tiene ningún punto con posición
    assert set(tipicas["recorrido"]) == {"T101 00I", "T101 00R", "T202 00I", "T404 00I", "T505 00I", "T606 00I",
                                         "T707 00R"}
