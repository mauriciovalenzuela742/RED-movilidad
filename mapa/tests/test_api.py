import pytest
from fastapi.testclient import TestClient

from mapa_red.api import crear_app

from .conftest import DIA_LABORAL, PERIODO


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


def test_celdas_global_comprimida_al_arrancar(cliente):
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
