"""
Consultas del mapa (Polars): mapa de calor por celda y hora, rankings, celdas por recorrido, trayectoria
típica y trayectos de buses.

Todo sale de la caché que arma `mapa_red.cache`. Estas consultas no la construyen ni leen los parquet diarios,
así que basta con la carpeta de la caché para servir la página.
"""

from __future__ import annotations

import datetime as dt
import json
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polars as pl

from .cache import VERSION as VERSION_CACHE
from .config import Rutas
from .esquema import DIMENSIONES, SUMAS, ZONA

MIN_OBS_GLOBAL = 50        # registros mínimos por celda en la vista sin filtros (limpia ruido)
# En la vista por recorrido, el resto se agrupa como "Otros". 5 = los colores de la paleta categórica de la
# página (js/colores.js, PALETA): en un mapa, con más de 5 siempre hay dos colores que se confunden
MAX_RECORRIDOS_COLOR = 5
MAX_PUNTOS_TRAYECTOS = 250_000  # tope para "todos los buses" (un recorrido-día ronda 15–60 mil puntos)


class ConsultaInvalida(ValueError):
    """Pedido del usuario que no se puede responder (filtro faltante, día mal escrito…): la API la
    devuelve como 400 con este mensaje. Los demás errores siguen siendo 500."""


class Cache:
    """La caché de un período. Es lo único que leen las consultas."""

    def __init__(self, periodo: str, rutas: Rutas):
        self.periodo = periodo
        self.carpeta = rutas.carpeta_cache(periodo)
        comando = f"uv run mapa-cache {'--dia' if periodo.count('-') == 2 else '--mes'} {periodo}"
        if not (self.carpeta / "meta.json").exists():
            raise FileNotFoundError(f"No hay caché en {self.carpeta}. Constrúyela con: {comando}")
        self.meta = json.loads((self.carpeta / "meta.json").read_text(encoding="utf-8"))
        if self.meta.get("version") != VERSION_CACHE:
            raise FileNotFoundError(f"La caché de {self.carpeta} es de una versión anterior "
                                    f"({self.meta.get('version')}, se necesita {VERSION_CACHE}). Reconstrúyela con: {comando}")
        self.tam = self.meta["tam"]
        # Identifica esta construcción de la caché: las respuestas no cambian mientras sea la misma, así que
        # la página la pone en la URL de /vista (?v=…) y el navegador (o CloudFront) puede guardarlas
        self.identidad = str(int((self.carpeta / "meta.json").stat().st_mtime))
        self._grupo_de: dict[str, int] | None = None

    def grupo(self, patente: str) -> int | None:
        """El archivo agg/grupo=NN donde está la patente (se guardó al construir la caché). La tabla se lee una
        sola vez y queda en memoria, porque un mismo pedido la consulta varias veces."""
        if self._grupo_de is None:
            self._grupo_de = dict(pl.scan_parquet(self.carpeta / "ranking_patente.parquet")
                                  .select("patente", "grupo").collect().iter_rows())
        return self._grupo_de.get(patente)

    def agg(self, filtros: dict) -> pl.LazyFrame:
        """Celdas por recorrido × patente × hora × tipo de día, con los filtros dados. Con bus, lee solo el
        archivo de su grupo (1 de 16): ~7 veces más rápido que recorrer todos."""
        g = self.grupo(filtros["patente"]) if filtros.get("patente") else None
        lf = pl.scan_parquet(self.carpeta / "agg" / ("*.parquet" if g is None else f"grupo={g:02d}.parquet"))
        for dim, valor in filtros.items():
            lf = lf.filter(pl.col(dim) == valor)
        return lf

    def dias_bus(self) -> pl.LazyFrame:
        """Cuántos registros con posición (`n`) tiene cada bus por día operativo y recorrido."""
        return pl.scan_parquet(self.carpeta / "dias_bus.parquet")

    def trayectos(self, d0: dt.date) -> Path:
        """El archivo con los puntos GPS del día operativo `d0`."""
        return self.carpeta / "trayectos" / f"dia={d0.isoformat()}.parquet"


def fecha(dia: str) -> dt.date:
    try:
        return dt.date.fromisoformat(dia)
    except (TypeError, ValueError):
        raise ConsultaInvalida(f"Día inválido: {dia} (formato YYYY-MM-DD)") from None


def _con_algun_filtro(filtros: dict, dims=("servicio", "sentido", "recorrido")) -> None:
    if not any(filtros.get(d) for d in dims):
        raise ConsultaInvalida("Hay que filtrar por servicio, sentido o recorrido")


def _condicion(filtros: dict) -> pl.Expr:
    """Filtro de servicio / sentido / recorrido sobre filas con esas columnas."""
    cond = pl.lit(True)
    for dim in ("servicio", "sentido", "recorrido"):
        if filtros.get(dim):
            cond = cond & (pl.col(dim) == filtros[dim])
    return cond


# ── Mapa de calor ────────────────────────────────────────────────────────────

TIPOS_BINARIOS = {"Float32": ("float32", 4), "UInt32": ("uint32", 4), "UInt16": ("uint16", 2), "UInt8": ("uint8", 1)}


def entero_minimo(serie: pl.Series) -> pl.Series:
    """Conteo como UInt16 si cabe (la mitad de bytes en la respuesta), si no UInt32. Una celda × hora de un
    terminal llega a ~95 000 registros en un mes: con UInt16 fijo se recortaba a 65 535."""
    return serie.cast(pl.UInt16 if (serie.max() or 0) <= 65_535 else pl.UInt32)


def _paquete(cabecera: dict, arreglos: dict[str, pl.Series], json_crudo: dict[str, str] | None = None) -> bytes:
    """[uint32 largo][cabecera JSON][arreglos alineados a 4 bytes], el formato que lee la página. Los bytes
    de cada arreglo salen del búfer Arrow de la serie de Polars, sin pasar por listas de Python.
    `json_crudo`: más campos para la cabecera, ya escritos como JSON (por Polars con `write_json()`): se
    insertan tal cual, sin volver a pasar por diccionarios de Python."""
    partes, indice, offset = [], {}, 0
    for nombre, serie in arreglos.items():
        tipo, ancho = TIPOS_BINARIOS[str(serie.dtype)]
        relleno = (-offset) % 4
        partes.append(b"\0" * relleno)
        offset += relleno
        arrow = serie.rechunk().to_arrow()
        datos = arrow.buffers()[1].to_pybytes()[arrow.offset * ancho:(arrow.offset + len(serie)) * ancho]
        indice[nombre] = {"offset": offset, "len": len(serie), "tipo": tipo}
        partes.append(datos)
        offset += len(datos)
    meta = json.dumps({**cabecera, "indice": indice})
    if json_crudo:  # "{...}" → "{..., "clave": <json>, ...}"
        meta = meta[:-1] + "".join(f",{json.dumps(k)}:{v}" for k, v in json_crudo.items()) + "}"
    meta = meta.encode()
    meta += b" " * ((-(4 + len(meta))) % 4)
    return struct.pack("<I", len(meta)) + meta + b"".join(partes)


def celdas(cache: Cache, filtros: dict) -> bytes:
    """Matrices [tipo de día (2), hora (24), celda] de registros, velocidad y velocidad comercial: con ellas
    la página elige y anima la hora sin volver a pedir datos."""
    return _paquete(*_matrices_celdas(cache, filtros))


def _matrices_celdas(cache: Cache, filtros: dict) -> tuple[dict, dict[str, pl.Series]]:
    """Cabecera (celdas y buses) y matrices del mapa de calor, sin empaquetar."""
    if filtros:
        lf = cache.agg(filtros)
        planes = [lf.group_by("ix", "iy", "tipo_dia", "hora").agg(pl.col(SUMAS).sum()),
                  lf.select(pl.col("patente").n_unique())]
    else:  # vista global: ya agregada en la caché, sin celdas de muy pocas observaciones
        planes = [pl.scan_parquet(cache.carpeta / "celdas_global.parquet")
                    .filter(pl.col("n_total").sum().over("ix", "iy") >= MIN_OBS_GLOBAL),
                  pl.scan_parquet(cache.carpeta / "ranking_patente.parquet").select(pl.len())]
    f, buses = pl.collect_all(planes)

    ids = f.lazy().select("ix", "iy").unique().sort("ix", "iy").with_row_index("id").collect()
    nc = ids.height
    n_mov, s_ruta = pl.col("n_mov"), pl.col("s_ruta")
    m = (f.lazy().join(ids.lazy(), on=["ix", "iy"])
         .select(idx=(pl.col("tipo_dia").cast(pl.Int64) * 24 + pl.col("hora").cast(pl.Int64)) * nc + pl.col("id"),
                 n_total="n_total",
                 n_mov=n_mov,
                 # velocidades con resolución de 0,5 km/h
                 vel=pl.when(n_mov > 0).then(pl.col("suma_vel") / n_mov * 2).otherwise(0).round(0).clip(0, 255),
                 vcom=pl.when(s_ruta > 0).then(pl.col("km_ruta") * 3600 / s_ruta * 2).otherwise(0).round(0).clip(0, 255),
                 scom=(s_ruta / 10).round(0))  # peso de la vel. comercial: segundos en ruta / 10
         .collect())
    n = 2 * 24 * nc

    def matriz(valores: pl.Series) -> pl.Series:
        return pl.zeros(n, dtype=valores.dtype, eager=True).scatter(m["idx"], valores)

    return {"n_celdas": nc, "buses": int(buses.item())}, {
        "lon": ids.select(((pl.col("ix") + 0.5) * cache.tam).cast(pl.Float32)).to_series(),
        "lat": ids.select(((pl.col("iy") + 0.5) * cache.tam).cast(pl.Float32)).to_series(),
        "n_total": matriz(entero_minimo(m["n_total"])), "n_mov": matriz(entero_minimo(m["n_mov"])),
        "vel": matriz(m["vel"].cast(pl.UInt8)), "vcom": matriz(m["vcom"].cast(pl.UInt8)),
        "scom": matriz(entero_minimo(m["scom"])),
    }


# ── Ranking y vista por recorrido ────────────────────────────────────────────

def _metricas(lf: pl.LazyFrame, grupo: str) -> pl.LazyFrame:
    """Sumas por grupo → métricas, ordenadas por cantidad de registros (de más a menos)."""
    return lf.select(
        id=pl.col(grupo),
        n=pl.col("n_total"),
        vel_mov=(pl.col("suma_vel") / pl.col("n_mov")).fill_nan(0).fill_null(0).round(1),
        pct_det=(100 * (1 - pl.col("n_mov") / pl.col("n_total"))).round(1),
        buses=pl.col("buses"),
        vel_com=pl.when(pl.col("s_ruta") >= 600)  # al menos 10 min en ruta para informarla
                  .then(pl.col("km_ruta").cast(pl.Float64) * 3600 / pl.col("s_ruta").cast(pl.Float64)).round(1),
    ).sort("n", descending=True)


def _resumen_por_recorrido(cache: Cache, filtros: dict) -> pl.LazyFrame:
    return _metricas(cache.agg(filtros).group_by("recorrido")
                     .agg(pl.col(SUMAS).sum(), buses=pl.col("patente").n_unique()), "recorrido")


def ranking(cache: Cache, grupo: str, filtros: dict) -> pl.DataFrame:
    """Métricas por servicio, sentido, recorrido o patente, respetando los demás filtros activos."""
    if grupo not in DIMENSIONES:
        raise ConsultaInvalida(f"Grupo inválido: {grupo}")
    otros = {k: v for k, v in filtros.items() if k != grupo}
    if not otros:
        lf = pl.scan_parquet(cache.carpeta / f"ranking_{grupo}.parquet")
    else:
        lf = cache.agg(otros).group_by(grupo).agg(pl.col(SUMAS).sum(), buses=pl.col("patente").n_unique())
    return _metricas(lf, grupo).collect()


def por_recorrido(cache: Cache, filtros: dict) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Celdas separadas por recorrido (todo el período), para colorear cada uno distinto: los 5 con más
    registros y el resto como "Otros". Devuelve (leyenda, celdas); cada celda con i = índice de su
    recorrido en la leyenda, x / y = lon / lat, n = registros, v = km/h en movimiento, d = % detenido."""
    if not filtros:
        raise ConsultaInvalida("La vista por recorrido requiere al menos un filtro")
    resumen = _resumen_por_recorrido(cache, filtros).collect()
    principales = resumen["id"].head(MAX_RECORRIDOS_COLOR).to_list()
    orden = {r: i for i, r in enumerate(principales + ["Otros"])}
    tam = cache.tam
    celdas_rec = (cache.agg(filtros)
                  .with_columns(rec=pl.when(pl.col("recorrido").is_in(principales))
                                .then(pl.col("recorrido")).otherwise(pl.lit("Otros")))
                  .group_by("rec", "ix", "iy").agg(pl.col(SUMAS).sum())
                  .select(i=pl.col("rec").replace_strict(orden, return_dtype=pl.Int32),
                          x=((pl.col("ix") + 0.5) * tam).round(5),
                          y=((pl.col("iy") + 0.5) * tam).round(5),
                          n=pl.col("n_total"),
                          v=pl.when(pl.col("n_mov") > 0).then(pl.col("suma_vel") / pl.col("n_mov"))
                              .otherwise(0).round(1),
                          d=(100 * (1 - pl.col("n_mov") / pl.col("n_total"))).round(1))
                  .collect())
    leyenda = resumen.head(MAX_RECORRIDOS_COLOR)
    if resumen.height > MAX_RECORRIDOS_COLOR:  # el resto, en una fila "Otros"
        resto = resumen.slice(MAX_RECORRIDOS_COLOR)
        leyenda = pl.concat([leyenda, pl.DataFrame({"id": ["Otros"], "n": [resto["n"].sum()],
                                                    "otros": [resto.height]})], how="diagonal_relaxed")
    return leyenda, celdas_rec


def por_recorrido_json(cache: Cache, filtros: dict) -> str:
    """`por_recorrido` como lo recibe la página: {"recorridos": leyenda, "celdas": celdas}."""
    leyenda, celdas_rec = por_recorrido(cache, filtros)
    return f'{{"recorridos":{leyenda.write_json()},"celdas":{celdas_rec.write_json()}}}'


# ── Trayectos: los puntos GPS de un día (cache/trayectos/) ───────────────────

def _columnas_trayecto(d0: dt.date) -> list[pl.Expr]:
    """Columnas que recibe la página por punto: hora local, `t` = segundos desde la medianoche del día
    (siguen contando después de las 00:00, para reproducir en orden), lon / lat y km/h."""
    local = pl.col("ts").dt.convert_time_zone(ZONA)
    segundos = ((local.dt.date() > pl.lit(d0)).cast(pl.Int32) * 86_400 + local.dt.hour().cast(pl.Int32) * 3600
                + local.dt.minute().cast(pl.Int32) * 60 + local.dt.second().cast(pl.Int32))
    return [local.dt.strftime("%H:%M:%S").alias("hora"), segundos.alias("t"),
            pl.col("lon").cast(pl.Float64).round(5).alias("x"), pl.col("lat").cast(pl.Float64).round(5).alias("y"),
            pl.col("speed").cast(pl.Float64).round(1).alias("v")]


def dias_bus(cache: Cache, patente: str | None, filtros: dict) -> pl.DataFrame:
    """Días operativos con datos. Con bus: `n` = sus registros del día y `n_filtro` = los que calzan con los
    filtros de servicio / sentido / recorrido. Sin bus: días en que alguien hizo lo filtrado (`n` = `n_filtro`).
    Solo se listan los días con alguno."""
    if not patente:
        _con_algun_filtro(filtros)
    cond = _condicion(filtros)
    lf = cache.dias_bus()
    if patente:
        lf = lf.filter(pl.col("patente") == patente)
    return (lf.group_by("dia").agg(n=pl.col("n").filter(pl.lit(True) if patente else cond).sum(),
                                   n_filtro=pl.col("n").filter(cond).sum())
            .filter(pl.col("n_filtro") > 0)
            .sort("dia")
            .select(pl.col("dia").cast(pl.String), "n", "n_filtro")
            .collect())


def trayecto(cache: Cache, patente: str, dia: str, filtros: dict | None = None) -> pl.DataFrame:
    """
    Los puntos GPS del bus en el día operativo, en orden: hora, `t`, lon / lat, km/h, recorrido y viaje. Los
    viajes vienen numerados desde la caché, con el día completo del bus. Con filtros de servicio, sentido o
    recorrido, solo devuelve la parte del día que calza.
    """
    d0 = fecha(dia)
    # Primero se buscan en dias_bus los recorridos que hizo el bus ese día. Como el archivo del día está
    # ordenado por recorrido, filtrar también por ellos permite leer solo los pedazos donde está el bus.
    # Buscarlo solo por la patente obliga a recorrer el archivo entero (unas 6 veces más lento). La lista se
    # trae a Python a propósito: con un join lazy, Polars no se salta ningún pedazo.
    sus_recorridos = (cache.dias_bus()
                      .filter((pl.col("patente") == patente) & (pl.col("dia") == d0) & _condicion(filtros or {}))
                      .select("recorrido").collect().to_series().to_list())
    if not sus_recorridos:
        return pl.DataFrame()
    return (pl.scan_parquet(cache.trayectos(d0))
            .filter((pl.col("patente") == patente) & pl.col("recorrido").is_in(sus_recorridos))
            .sort("ts")
            .select(*_columnas_trayecto(d0), "recorrido", "viaje")
            .collect())


def trayectos(cache: Cache, filtros: dict, dia: str) -> pl.DataFrame:
    """
    Los puntos GPS de todos los buses que hicieron el servicio, sentido o recorrido filtrado ese día,
    ordenados por bus y hora. Trae las mismas columnas que `trayecto` y además la patente. Los viajes son los
    mismos de cada bus, pero renumerados para que ningún número se repita entre buses.
    """
    _con_algun_filtro(filtros)
    d0 = fecha(dia)
    cond = _condicion(filtros)
    # dias_bus ya sabe cuántos puntos son, así que un pedido demasiado grande se rechaza sin leer nada
    # (filtrando solo por sentido, un día tiene unos 7 millones)
    n = cache.dias_bus().filter((pl.col("dia") == d0) & cond).select(pl.col("n").sum()).collect().item()
    if n > MAX_PUNTOS_TRAYECTOS:
        raise ConsultaInvalida(f"{n:,} puntos: demasiados para dibujar. Filtra por sentido o recorrido.")
    if not n:
        return pl.DataFrame()
    otro_viaje = (pl.col("patente") != pl.col("patente").shift(1)) | (pl.col("viaje") != pl.col("viaje").shift(1))
    return (pl.scan_parquet(cache.trayectos(d0))
            .filter(cond)
            .sort("patente", "ts")
            .select(*_columnas_trayecto(d0), "recorrido", "patente",
                    viaje=otro_viaje.fill_null(True).cum_sum().cast(pl.Int32) - 1)
            .collect())


# ── Trayectoria típica (ya elegida al construir la caché) ────────────────────

def tipicas(cache: Cache, filtros: dict) -> pl.DataFrame:
    """La trayectoria típica (un viaje real) de los recorridos que se ven en la vista Recorridos, que son los 5
    con más registros. Ya vienen elegidas en la caché: aquí solo se buscan."""
    if not filtros:
        raise ConsultaInvalida("La trayectoria típica requiere al menos un filtro")
    principales = _resumen_por_recorrido(cache, filtros).head(MAX_RECORRIDOS_COLOR).select(recorrido="id")
    return (pl.scan_parquet(cache.carpeta / "tipicas.parquet")
            .join(principales, on="recorrido", how="semi")
            .collect())


# ── Vista: todo lo que cambia con los filtros, en una respuesta ──────────────

def vista(cache: Cache, filtros: dict, con_dias: bool = False) -> bytes:
    """
    Todo lo que la página necesita al cambiar los filtros, en una sola respuesta (en Lambda: un solo
    arranque en frío y una sola invocación, en vez de una por pedido): el paquete binario del mapa de calor
    (`celdas`) con, en su cabecera, `listas` (métricas por servicio, sentido, recorrido y patente: los
    desplegables y el ranking), `por_recorrido` (con algún filtro) y `dias` (con `con_dias`).

    Son las mismas consultas de los endpoints por separado, en paralelo: Polars suelta el GIL mientras
    consulta, así que los hilos se superponen (y en S3, sus esperas por la red también).
    """
    tareas = {"celdas": lambda: _matrices_celdas(cache, filtros)}
    for g in DIMENSIONES:
        tareas[g] = lambda g=g: ranking(cache, g, filtros).write_json()
    if filtros:
        tareas["por_recorrido"] = lambda: por_recorrido_json(cache, filtros)
    if con_dias:
        tareas["dias"] = lambda: dias_bus(cache, filtros.get("patente"), filtros).write_json()
    with ThreadPoolExecutor(max_workers=len(tareas)) as hilos:
        en_curso = {nombre: hilos.submit(tarea) for nombre, tarea in tareas.items()}
        r = {nombre: f.result() for nombre, f in en_curso.items()}  # un error en una tarea sube tal cual
    cabecera, matrices = r["celdas"]
    return _paquete(cabecera, matrices, {
        "listas": "{" + ",".join(f'"{g}":{r[g]}' for g in DIMENSIONES) + "}",
        "por_recorrido": r.get("por_recorrido", "null"),
        "dias": r.get("dias", "null"),
    })
