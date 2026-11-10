"""Exportación de capas de referencia para el mapa (usa las tablas de data/derived y data/external del repo)."""
from pulso import capas_mapa as cm


def test_servicio_mapa_regla_provisional():
    assert cm.servicio_mapa("405") == "T405"
    assert cm.servicio_mapa("219e") == "T219e"
    assert cm.servicio_mapa("B20") == "B20"


def test_cargadores_con_posicion():
    filas = cm.cargadores()
    # el extracto OSM incluye también la costa (Valparaíso, San Antonio): caja de Chile central
    assert filas and all(-72 < f["pos"][0] < -70 and -34.5 < f["pos"][1] < -32.5 for f in filas)


def test_trazados_simplificados_con_servicio_y_sentido():
    filas = cm.trazados()
    assert all(len(t["camino"]) >= 2 and t["sentido"] in ("I", "R") for t in filas)
    assert any(t["servicio"] == "T405" for t in filas)
