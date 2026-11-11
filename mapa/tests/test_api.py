import json
import re
import shutil
import struct

import pytest
from fastapi.testclient import TestClient

from mapa_red.api import crear_app
from mapa_red.config import Rutas

from .conftest import DIA_LABORAL, PERIODO


def leer_paquete(cuerpo: bytes) -> tuple[dict, bytes]:
    """[uint32 largo][cabecera JSON][arreglos] → (cabecera, bytes de los arreglos)"""
    largo = struct.unpack("<I", cuerpo[:4])[0]
    return json.loads(cuerpo[4:4 + largo]), cuerpo[4 + largo:]


def ordenadas(filas: list | None) -> list | None:
    """Filas en un orden fijo (los empates y los group_by de Polars no garantizan uno)"""
    return None if filas is None else sorted(filas, key=lambda f: json.dumps(f, sort_keys=True))


@pytest.fixture(scope="module")
def cliente(rutas):
    with TestClient(crear_app(PERIODO, rutas)) as c:  # `with`: corre el arranque (carga la caché)
        yield c


def test_pagina_y_estaticos(cliente):
    assert "Velocidad de buses RED" in cliente.get("/").text
    for ruta in ("/static/js/main.js", "/static/css/estilos.css", "/static/vendor/deck.gl.min.js"):
        assert cliente.get(ruta).status_code == 200, ruta


def test_config_js(cliente):
    r = cliente.get("/config.js")
    assert r.headers["content-type"].startswith("text/javascript")
    assert r.text.startswith("window.CONFIG = ") and '"periodo": "2025-09"' in r.text


def test_celdas(cliente):
    for params in ({}, {"servicio": "T101"}):
        r = cliente.get("/celdas", params=params)
        assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"


@pytest.mark.parametrize("filtros", [{"servicio": "T101"}, {"patente": "AAAA11", "recorrido": "T101 00R"}])
def test_vista_reune_lo_mismo_que_los_endpoints_por_separado(cliente, filtros):
    cabecera, matrices = leer_paquete(cliente.get("/vista", params={**filtros, "dias": 1}).content)
    # el mapa: las mismas matrices que /celdas
    cab_celdas, mat_celdas = leer_paquete(cliente.get("/celdas", params=filtros).content)
    assert {k: cabecera[k] for k in ("n_celdas", "buses", "indice")} == cab_celdas and matrices == mat_celdas
    # las listas de los filtros y el ranking: lo mismo que /ranking por cada dimensión
    for g in ("servicio", "sentido", "recorrido", "patente"):
        por_separado = cliente.get("/ranking", params={**filtros, "grupo": g}).json()
        assert ordenadas(cabecera["listas"][g]) == ordenadas(por_separado), g
    rec = cliente.get("/por_recorrido", params=filtros).json()
    assert cabecera["por_recorrido"]["recorridos"] == rec["recorridos"]
    assert ordenadas(cabecera["por_recorrido"]["celdas"]) == ordenadas(rec["celdas"])
    assert cabecera["dias"] == cliente.get("/dias", params=filtros).json()


def test_vista_sin_dias_no_los_trae(cliente):
    cabecera, _ = leer_paquete(cliente.get("/vista", params={"servicio": "T101"}).content)
    assert cabecera["dias"] is None and cabecera["por_recorrido"] is not None


def test_vista_global_comprimida_al_arrancar(cliente):
    comprimida = cliente.get("/vista", headers={"Accept-Encoding": "gzip"})
    plana = cliente.get("/vista", headers={"Accept-Encoding": "identity"})
    assert comprimida.headers["content-encoding"] == "gzip" and "content-encoding" not in plana.headers
    assert comprimida.content == plana.content
    cabecera, _ = leer_paquete(plana.content)
    assert cabecera["por_recorrido"] is None and cabecera["dias"] is None
    assert {f["id"] for f in cabecera["listas"]["servicio"]} >= {"T101", "T202"}


def test_vista_se_puede_guardar_solo_con_la_cache_vigente(cliente):
    vigente = re.search(r'"cache": "(\d+)"', cliente.get("/config.js").text).group(1)
    for params in ({}, {"servicio": "T101"}):
        assert cliente.get("/vista", params={**params, "v": vigente}).headers["cache-control"] == \
            "public, max-age=86400, immutable"
        assert "cache-control" not in cliente.get("/vista", params={**params, "v": "otra"}).headers
        assert "cache-control" not in cliente.get("/vista", params=params).headers


def test_vista_dias_sin_filtros_es_400(cliente):
    r = cliente.get("/vista", params={"dias": 1})
    assert r.status_code == 400 and "Hay que filtrar" in r.json()["detail"]


def test_celdas_global_comprimida(cliente):
    comprimida = cliente.get("/celdas", headers={"Accept-Encoding": "gzip"})
    plana = cliente.get("/celdas", headers={"Accept-Encoding": "identity"})
    assert comprimida.headers["content-encoding"] == "gzip" and "content-encoding" not in plana.headers
    assert comprimida.content == plana.content  # httpx la descomprime: el mismo contenido


def test_la_pagina_lee_todos_los_tipos_binarios_de_celdas():
    # /celdas declara el tipo de cada arreglo (consultas.TIPOS_BINARIOS); si la página no conoce uno, no carga
    # ("TIPOS[v.tipo] is not a constructor")
    from pathlib import Path

    from mapa_red.api import ESTATICO
    from mapa_red.consultas import TIPOS_BINARIOS

    js = (Path(ESTATICO) / "js" / "calor.js").read_text(encoding="utf-8")
    for tipo, _ in TIPOS_BINARIOS.values():
        assert f"{tipo}:" in js, f"js/calor.js no sabe leer {tipo}"


def test_ranking(cliente):
    assert {f["id"] for f in cliente.get("/ranking", params={"grupo": "servicio"}).json()} >= {"T101", "T202"}
    r = cliente.get("/ranking", params={"grupo": "chofer"})
    assert r.status_code == 400 and "Grupo inválido" in r.json()["detail"]


def test_trayecto(cliente):
    r = cliente.get("/trayecto", params={"patente": "AAAA11", "dia": DIA_LABORAL, "recorrido": "T101 00R"})
    assert r.status_code == 200
    assert {p["recorrido"] for p in r.json()} == {"T101 00R"}


def test_basta_con_la_carpeta_de_la_cache(rutas, tmp_path):
    # Se copia solo la carpeta de la caché a otro lugar, sin los parquet diarios: es justo lo que se despliega
    carpeta = rutas.carpeta_cache(PERIODO)
    shutil.copytree(carpeta, tmp_path / carpeta.name)
    with TestClient(crear_app(PERIODO, Rutas(datos_local=tmp_path / "no-existe", cache_base=tmp_path))) as c:
        assert c.get("/vista", params={"servicio": "T101", "dias": 1}).status_code == 200
        assert len(c.get("/trayecto", params={"patente": "AAAA11", "dia": DIA_LABORAL}).json()) == 240
        assert len(c.get("/trayectos", params={"servicio": "T101", "dia": DIA_LABORAL}).json()) == 240
        assert {t["recorrido"] for t in c.get("/tipicas", params={"servicio": "T101"}).json()} == {"T101 00I", "T101 00R"}


def test_errores_de_entrada_son_400_con_mensaje(cliente):
    casos = [("/trayecto", {"patente": "AAAA11", "dia": "2025-02-30"}, "Día inválido"),
             ("/trayectos", {"dia": DIA_LABORAL}, "Hay que filtrar"),
             ("/dias", {}, "Hay que filtrar"),
             ("/por_recorrido", {}, "requiere al menos un filtro"),
             ("/tipicas", {}, "requiere al menos un filtro")]
    for ruta, params, mensaje in casos:
        r = cliente.get(ruta, params=params)
        assert r.status_code == 400, ruta
        assert mensaje in r.json()["detail"], ruta


def test_capas_de_referencia(rutas, tmp_path):
    """Solo se ofrecen y sirven las capas conocidas que existen; sin carpeta, el mapa funciona sin ellas."""
    import dataclasses
    import json

    (tmp_path / "trazados.json").write_text(json.dumps([{"id": "405I", "camino": [[-70.6, -33.4]]}]))
    (tmp_path / "otra.json").write_text("[]")  # desconocida: no se sirve
    with TestClient(crear_app(PERIODO, dataclasses.replace(rutas, capas=tmp_path))) as c:
        assert '"capas": ["trazados"]' in c.get("/config.js").text
        assert c.get("/capas/trazados.json").json()[0]["id"] == "405I"
        assert c.get("/capas/pendiente.json").status_code == 404  # conocida pero sin archivo
        assert c.get("/capas/otra.json").status_code == 404
    with TestClient(crear_app(PERIODO, rutas)) as c:  # rutas sin capas
        assert '"capas": []' in c.get("/config.js").text
        assert c.get("/capas/trazados.json").status_code == 404
