"""
API del mapa de velocidad (FastAPI): sirve la página (`static/`) y sus consultas.

La página pide `/vista` (todo lo que cambia con los filtros, en una respuesta) y, aparte, el trayecto y las
trayectorias típicas. Los demás endpoints (`/celdas`, `/ranking`, `/por_recorrido`, `/dias`) devuelven cada
parte por separado. La vista sin filtros se calcula y comprime una vez al arrancar. Todo sale de la carpeta
de la caché: la API no lee los parquet diarios.
Las respuestas JSON las escribe Polars (`df.write_json()`, desde Rust): 5–8 veces más rápido que pasar
por diccionarios de Python y `JSONResponse` con estas tablas.

Uso (necesita la caché que arma `uv run mapa-cache`):
    uv run mapa-api                          # período 2025-09 → http://127.0.0.1:8900
    uv run mapa-api --periodo 2025-09-15
    uv run uvicorn mapa_red.api:app          # período desde la variable de entorno PERIODO
"""

from __future__ import annotations

import argparse
import gzip
import json
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import consultas
from .config import Rutas, periodo_por_defecto
from .consultas import ConsultaInvalida

ESTATICO = Path(__file__).resolve().parent / "static"
# Capas de referencia que conoce la página (js/referencia.js); se ofrecen solo las que existen en Rutas.capas
CAPAS_REFERENCIA = ("trazados", "pendiente", "paraderos", "cargadores")


def capas_disponibles(rutas: Rutas) -> list[str]:
    if rutas.capas is None:
        return []
    return [c for c in CAPAS_REFERENCIA if (rutas.capas / f"{c}.json").is_file()]


def _filtros(servicio: str | None = None, sentido: str | None = None, recorrido: str | None = None,
             patente: str | None = None) -> dict:
    return {k: v for k, v in dict(servicio=servicio, sentido=sentido, recorrido=recorrido,
                                  patente=patente).items() if v}


def _json(cuerpo: str | bytes) -> Response:
    """JSON ya escrito por Polars."""
    return Response(cuerpo, media_type="application/json")


BINARIO = "application/octet-stream"


def _binario(request: Request, plano: bytes, comprimido: bytes, cabeceras: dict | None = None) -> Response:
    """Respuesta binaria ya comprimida con gzip si el navegador lo acepta (GZipMiddleware deja pasar las que
    traen Content-Encoding)."""
    cabeceras = {**(cabeceras or {}), "Vary": "Accept-Encoding"}
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(comprimido, media_type=BINARIO, headers={**cabeceras, "Content-Encoding": "gzip"})
    return Response(plano, media_type=BINARIO, headers=cabeceras)


def _gzip(datos: bytes) -> bytes:
    # Nivel 6: casi lo mismo que el 9 en la sexta parte del tiempo (14 MB: 2,4 s en vez de 16 s)
    return gzip.compress(datos, compresslevel=6)


def crear_app(periodo: str | None = None, rutas: Rutas | None = None) -> FastAPI:
    periodo = periodo or periodo_por_defecto()
    rutas = rutas or Rutas.desde_entorno()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        cache = consultas.Cache(periodo, rutas)
        dias = cache.meta["dias"]
        app.state.cache = cache
        # La vista sin filtros (lo primero que ve todo el mundo), calculada y comprimida una vez aquí: son
        # ~14 MB y comprimirlos en cada pedido tardaba 16 s con el nivel 9 por defecto de GZipMiddleware
        app.state.vista_global = consultas.vista(cache, {})
        app.state.vista_global_gz = _gzip(app.state.vista_global)
        app.state.celdas_global = None  # /celdas sin filtros: se calcula la primera vez que alguien la pide
        app.state.config_js = "window.CONFIG = " + json.dumps({
            "periodo": periodo, "tam": cache.tam, "cache": cache.identidad,
            "dias_laboral": int(dias.get("0", 0)), "dias_finde": int(dias.get("1", 0)),
            "capas": capas_disponibles(rutas)}) + ";"
        yield

    app = FastAPI(title="Velocidad buses RED", lifespan=ciclo_de_vida)
    # Nivel 6: comprime casi igual que el 9 (por defecto) en la sexta parte del tiempo. Las respuestas que ya
    # traen Content-Encoding (la vista global de /celdas) pasan sin volver a comprimirse.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    @app.exception_handler(ConsultaInvalida)
    async def consulta_invalida(_: Request, e: ConsultaInvalida):
        return JSONResponse({"detail": str(e)}, status_code=400)

    def cache() -> consultas.Cache:
        return app.state.cache

    @app.get("/config.js")
    def config_js():
        return Response(app.state.config_js, media_type="text/javascript")

    @app.get("/vista")
    def vista(request: Request, servicio: str | None = None, sentido: str | None = None,
              recorrido: str | None = None, patente: str | None = None, dias: bool = False,
              v: str | None = None):
        """Todo lo que cambia con los filtros, en una respuesta: el mapa de calor (binario) y, en la cabecera,
        las listas de los filtros, el ranking, la vista por recorrido y (con `dias=1`) los días con datos.
        Con `v` igual a la caché vigente, la respuesta no cambia mientras la caché sea la misma: el
        navegador (y CloudFront) la pueden guardar."""
        f = _filtros(servicio, sentido, recorrido, patente)
        guardable = {"Cache-Control": "public, max-age=86400, immutable"} if v == cache().identidad else {}
        if not f and not dias:
            return _binario(request, app.state.vista_global, app.state.vista_global_gz, guardable)
        return Response(consultas.vista(cache(), f, con_dias=dias), media_type=BINARIO, headers=guardable)

    @app.get("/celdas")
    def celdas(request: Request, servicio: str | None = None, sentido: str | None = None,
               recorrido: str | None = None, patente: str | None = None):
        f = _filtros(servicio, sentido, recorrido, patente)
        if f:
            return Response(consultas.celdas(cache(), f), media_type=BINARIO)
        if app.state.celdas_global is None:
            plano = consultas.celdas(cache(), {})
            app.state.celdas_global = (plano, _gzip(plano))
        return _binario(request, *app.state.celdas_global)

    @app.get("/ranking")
    def ranking(grupo: str = "recorrido", servicio: str | None = None, sentido: str | None = None,
                recorrido: str | None = None, patente: str | None = None):
        return _json(consultas.ranking(cache(), grupo, _filtros(servicio, sentido, recorrido, patente)).write_json())

    @app.get("/por_recorrido")
    def por_recorrido(servicio: str | None = None, sentido: str | None = None, recorrido: str | None = None,
                      patente: str | None = None):
        return _json(consultas.por_recorrido_json(cache(), _filtros(servicio, sentido, recorrido, patente)))

    @app.get("/tipicas")
    def tipicas(servicio: str | None = None, sentido: str | None = None, recorrido: str | None = None,
                patente: str | None = None):
        """Trayectoria típica (un viaje real) de cada recorrido de la vista Recorridos."""
        return _json(consultas.tipicas(cache(), _filtros(servicio, sentido, recorrido, patente)).write_json())

    @app.get("/dias")
    def dias(patente: str | None = None, servicio: str | None = None, sentido: str | None = None,
             recorrido: str | None = None):
        """Días operativos con datos del bus o, sin bus, de lo filtrado (servicio / sentido / recorrido)."""
        return _json(consultas.dias_bus(cache(), patente, _filtros(servicio, sentido, recorrido)).write_json())

    @app.get("/trayecto")
    def trayecto(patente: str, dia: str, servicio: str | None = None, sentido: str | None = None,
                 recorrido: str | None = None):
        """Puntos GPS del bus en un día operativo, en orden. Con servicio / sentido / recorrido, solo esa
        parte de su día."""
        return _json(consultas.trayecto(cache(), patente, dia, _filtros(servicio, sentido, recorrido)).write_json())

    @app.get("/trayectos")
    def trayectos(dia: str, servicio: str | None = None, sentido: str | None = None, recorrido: str | None = None):
        """Puntos GPS de todos los buses que hicieron lo filtrado ese día (con su patente)."""
        return _json(consultas.trayectos(cache(), _filtros(servicio, sentido, recorrido), dia).write_json())

    @app.get("/capas/{nombre}.json")
    def capa(nombre: str):
        """Capa de referencia (trazados GTFS, pendiente, paraderos, cargadores), tal como la exportó
        `python -m pulso.capas_mapa`. Solo los nombres conocidos: nada de rutas arbitrarias del disco."""
        if nombre not in capas_disponibles(rutas):
            return JSONResponse({"detail": f"Capa no disponible: {nombre}"}, status_code=404)
        return FileResponse(rutas.capas / f"{nombre}.json", media_type="application/json")

    @app.get("/")
    def pagina():
        return FileResponse(ESTATICO / "index.html")

    app.mount("/static", StaticFiles(directory=ESTATICO), name="static")
    return app


# Para `uvicorn mapa_red.api:app` (período y rutas desde el entorno; la caché se carga al arrancar)
app = crear_app()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="API y página del mapa de velocidad de buses RED")
    ap.add_argument("--periodo", default=periodo_por_defecto(), help="Mes (YYYY-MM) o día (YYYY-MM-DD) de la caché")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--puerto", type=int, default=8900)
    args = ap.parse_args(argv)
    uvicorn.run(crear_app(args.periodo), host=args.host, port=args.puerto)


if __name__ == "__main__":
    main()
