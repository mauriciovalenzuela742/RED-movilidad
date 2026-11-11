import json
import struct

import polars as pl
import pytest

from mapa_red import consultas
from mapa_red.config import Rutas
from mapa_red.consultas import Cache, ConsultaInvalida

from .conftest import DIA_LABORAL, PERIODO


@pytest.fixture(scope="module")
def cache(rutas) -> Cache:
    return Cache(PERIODO, rutas)


def test_cache_inexistente_explica_como_construirla(rutas):
    with pytest.raises(FileNotFoundError, match="mapa-cache --mes 2024-01"):
        Cache("2024-01", rutas)


def test_cache_de_version_anterior_pide_reconstruirla(tmp_path):
    rutas = Rutas(datos_local=tmp_path, cache_base=tmp_path)
    carpeta = rutas.carpeta_cache("2024-02")
    carpeta.mkdir()
    (carpeta / "meta.json").write_text(json.dumps({"version": 2, "tam": 0.001}), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="versión anterior.*mapa-cache --mes 2024-02"):
        Cache("2024-02", rutas)


def test_filtro_por_bus_lee_solo_su_grupo(cache):
    assert isinstance(cache.grupo("AAAA11"), int)
    assert cache.grupo("NO-EXISTE") is None
    # mismo resultado que recorriendo todos los archivos
    todos = (pl.scan_parquet(cache.carpeta / "agg" / "*.parquet").filter(pl.col("patente") == "AAAA11")
             .select(pl.col("n_total").sum()).collect().item())
    assert cache.agg({"patente": "AAAA11"}).select(pl.col("n_total").sum()).collect().item() == todos == 240
    assert consultas.ranking(cache, "recorrido", {"patente": "AAAA11"})["id"].sort().to_list() == ["T101 00I", "T101 00R"]


def test_paquete_inserta_json_crudo_en_la_cabecera():
    paquete = consultas._paquete({"n_celdas": 1}, {"x": pl.Series([1, 2], dtype=pl.UInt16)},
                                 {"listas": pl.DataFrame({"id": ["T101"], "n": [3]}).write_json(), "dias": "null"})
    largo = struct.unpack("<I", paquete[:4])[0]
    cabecera = json.loads(paquete[4:4 + largo])
    assert cabecera["listas"] == [{"id": "T101", "n": 3}] and cabecera["dias"] is None
    assert cabecera["n_celdas"] == 1 and cabecera["indice"]["x"] == {"offset": 0, "len": 2, "tipo": "uint16"}
    assert (4 + largo) % 4 == 0  # los arreglos quedan alineados para los TypedArray


def test_entero_minimo_no_recorta_conteos_grandes():
    assert consultas.entero_minimo(pl.Series([1, 65_535])).dtype == pl.UInt16
    assert consultas.entero_minimo(pl.Series([1, 95_634])).to_list() == [1, 95_634]  # antes: 65 535
    assert consultas.entero_minimo(pl.Series([], dtype=pl.Int32)).dtype == pl.UInt16


def test_celdas_formato_binario(cache):
    cuerpo = consultas.celdas(cache, {"recorrido": "T101 00I"})
    largo = struct.unpack("<I", cuerpo[:4])[0]
    cabecera = json.loads(cuerpo[4:4 + largo])
    assert cabecera["buses"] == 2
    nc = cabecera["n_celdas"]
    assert nc > 0
    assert cabecera["indice"]["n_total"]["len"] == 2 * 24 * nc  # [tipo de día, hora, celda]
    assert all(v["offset"] % 4 == 0 for v in cabecera["indice"].values())  # alineados para los TypedArray


def test_ranking_respeta_otros_filtros(cache):
    r = consultas.ranking(cache, "recorrido", {"servicio": "T101"})
    assert sorted(r["id"].to_list()) == ["T101 00I", "T101 00R"]
    with pytest.raises(ConsultaInvalida):
        consultas.ranking(cache, "chofer", {})


def test_por_recorrido(cache):
    leyenda, celdas = consultas.por_recorrido(cache, {"servicio": "T101"})
    assert leyenda["id"].to_list()[0] == "T101 00I"  # más registros primero
    assert set(celdas["i"].unique().to_list()) == {0, 1}
    with pytest.raises(ConsultaInvalida):
        consultas.por_recorrido(cache, {})


def test_por_recorrido_agrupa_en_otros_los_que_no_tienen_color(cache, monkeypatch):
    monkeypatch.setattr(consultas, "MAX_RECORRIDOS_COLOR", 3)
    leyenda, celdas = consultas.por_recorrido(cache, {"sentido": "I"})  # 5 recorridos de ida con posición
    assert leyenda["id"].to_list()[:3] == ["T505 00I", "T101 00I", "T202 00I"]  # los con más registros
    assert leyenda["id"][-1] == "Otros" and leyenda.filter(pl.col("id") == "Otros")["otros"].item() == 2
    assert celdas["i"].max() == 3  # las celdas del resto llevan el índice de "Otros"


def test_dias_bus(cache):
    dias = consultas.dias_bus(cache, "AAAA11", {"recorrido": "T101 00R"})
    assert dias.rows() == [(DIA_LABORAL, 240, 120)]  # 240 del bus ese día, 120 del regreso
    # sin bus: los días en que alguien hizo lo filtrado (miércoles y sábado)
    assert consultas.dias_bus(cache, None, {"recorrido": "T101 00I"})["dia"].to_list() == [DIA_LABORAL, "2025-09-13"]
    # un recorrido solo con registros sin posición no ofrece días (su trayecto saldría vacío)
    assert consultas.dias_bus(cache, None, {"recorrido": "T303 00I"}).is_empty()
    with pytest.raises(ConsultaInvalida):
        consultas.dias_bus(cache, None, {})


def test_trayecto_completo_y_filtrado(cache):
    todo = consultas.trayecto(cache, "AAAA11", DIA_LABORAL)
    assert todo.height == 240
    assert todo["recorrido"].unique().sort().to_list() == ["T101 00I", "T101 00R"]
    assert todo["viaje"].unique().sort().to_list() == [0, 1]  # se corta al cambiar de recorrido
    assert todo["hora"][0] == "09:00:00"  # hora local
    solo = consultas.trayecto(cache, "AAAA11", DIA_LABORAL, {"recorrido": "T101 00R"})
    assert solo.height == 120
    assert solo["recorrido"].unique().to_list() == ["T101 00R"]
    assert solo["viaje"].unique().to_list() == [1]  # conserva la numeración del día completo


def test_trayecto_corta_en_saltos_de_gps(cache):
    df = consultas.trayecto(cache, "FFFF66", DIA_LABORAL)
    # el registro que salta queda solo: no se dibujan las rectas de ida y vuelta hasta él
    assert df.group_by("viaje").len().sort("viaje")["len"].to_list() == [30, 1, 29]


def test_trayecto_dia_invalido(cache):
    with pytest.raises(ConsultaInvalida, match="Día inválido"):
        consultas.trayecto(cache, "AAAA11", "2025-02-30")


def test_trayecto_sin_datos_ese_dia(cache):
    assert consultas.trayecto(cache, "AAAA11", "2025-09-20").is_empty()


def test_trayectos_de_todos_los_buses(cache):
    df = consultas.trayectos(cache, {"servicio": "T101"}, DIA_LABORAL)
    assert df["patente"].unique().to_list() == ["AAAA11"]
    assert df.height == 240 and df["viaje"].unique().sort().to_list() == [0, 1]
    with pytest.raises(ConsultaInvalida):
        consultas.trayectos(cache, {}, DIA_LABORAL)


def test_trayectos_no_repite_un_numero_de_viaje_entre_buses(cache):
    df = consultas.trayectos(cache, {"sentido": "I"}, DIA_LABORAL)
    assert df["patente"].unique().sort().to_list() == ["AAAA11", "CCCC33", "DDDD44", "EEEE55", "FFFF66"]
    assert df.equals(df.sort("patente", "t", maintain_order=True))  # vienen ordenados por bus y hora
    # Cada número de viaje es de un solo bus y de un solo recorrido
    por_viaje = df.group_by("viaje").agg(pl.col("patente").n_unique(), pl.col("recorrido").n_unique())
    assert (por_viaje["patente"] == 1).all() and (por_viaje["recorrido"] == 1).all()
    # FFFF66 se corta en el salto de GPS. EEEE55 hace un viaje de punta a punta y, media hora después, la ida y
    # vuelta. DDDD44 parte con el punto que cae fuera de la ciudad, que también cuenta como salto.
    viajes_por_bus = dict(df.group_by("patente").agg(pl.col("viaje").n_unique()).iter_rows())
    assert viajes_por_bus == {"AAAA11": 1, "CCCC33": 1, "DDDD44": 2, "EEEE55": 2, "FFFF66": 3}


def test_trayectos_rechaza_los_pedidos_enormes_sin_leer_los_puntos(cache, monkeypatch):
    monkeypatch.setattr(consultas, "MAX_PUNTOS_TRAYECTOS", 100)
    # Si la consulta intentara leer el archivo del día, fallaría: tiene que decidir solo con dias_bus
    monkeypatch.setattr(cache, "trayectos", lambda d0: cache.carpeta / "no-existe.parquet")
    with pytest.raises(ConsultaInvalida, match="620 puntos: demasiados"):
        consultas.trayectos(cache, {"sentido": "I"}, DIA_LABORAL)


def test_dias_y_trayectos_cuentan_los_mismos_puntos(cache):
    # Si /dias ofrece un día, /trayecto tiene que traer puntos ese día, y tantos como dice /dias
    for (patente, dia), filas in cache.dias_bus().collect().group_by("patente", "dia"):
        assert consultas.trayecto(cache, patente, dia.isoformat()).height == filas["n"].sum(), (patente, dia)
        for recorrido, n in filas.select("recorrido", "n").iter_rows():
            assert consultas.trayecto(cache, patente, dia.isoformat(), {"recorrido": recorrido}).height == n


def test_trayectoria_tipica(cache):
    t = consultas.tipicas(cache, {"recorrido": "T202 00I"}).row(0, named=True)
    assert t["patente"] == "CCCC33" and t["dia"] == DIA_LABORAL
    assert t["inicio"] == "09:00" and t["n"] == 120
    assert len(t["camino"]) == 120 and len(t["camino"][0]) == 2
    assert consultas.tipicas(cache, {"servicio": "T101"})["recorrido"].sort().to_list() == ["T101 00I", "T101 00R"]


def test_trayectoria_tipica_prefiere_el_viaje_de_punta_a_punta(cache):
    t = consultas.tipicas(cache, {"recorrido": "T505 00I"}).row(0, named=True)
    assert (t["inicio"], t["n"]) == ("09:00", 120)  # no la ida y vuelta de 180 puntos que parte a las 10:30


def test_trayectoria_tipica_de_un_circuito_es_el_viaje_de_mas_puntos(cache):
    # T707 00R es un circuito: la vuelta completa termina donde partió (0 km de punta a punta) y el tramo corto
    # llega un poco más lejos (~0,5 km). Como ninguno alcanza 1 km, gana el de más puntos: la vuelta.
    t = consultas.tipicas(cache, {"recorrido": "T707 00R"}).row(0, named=True)
    assert (t["inicio"], t["fin"], t["n"]) == ("09:00", "10:29", 180)
