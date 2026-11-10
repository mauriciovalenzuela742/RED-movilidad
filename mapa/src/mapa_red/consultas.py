"""
Consultas del mapa (Polars): mapa de calor por celda y hora, rankings, celdas por recorrido, trayectoria
típica y trayectos de buses.

Lee la caché que arma `mapa_red.cache` (no la construye). La trayectoria típica y los trayectos salen de
los parquet diarios. Las reglas sobre los datos (servicio, sentido, día operativo) vienen de `esquema`.
"""

from __future__ import annotations

import datetime as dt
import json
import struct
from pathlib import Path

import polars as pl

from .cache import VERSION as VERSION_CACHE
from .config import Rutas
from .esquema import (
    CON_COORDENADAS,
    DIMENSIONES,
    PAUSA_NUEVO_VIAJE,
    RECORRIDO,
    SALTO_GPS,
    SENTIDO,
    SERVICIO,
    SUMAS,
    ZONA,
    dia_operativo,
    km_desde_anterior,
    km_entre,
)

MIN_OBS_GLOBAL = 50        # registros mínimos por celda en la vista sin filtros (limpia ruido)
# En la vista por recorrido, el resto se agrupa como "Otros". 5 = los colores de la paleta categórica de la
# página (js/colores.js, PALETA): en un mapa, con más de 5 siempre hay dos colores que se confunden
MAX_RECORRIDOS_COLOR = 5
MAX_PUNTOS_TRAYECTOS = 250_000  # tope para "todos los buses" (un recorrido-día ronda 15–60 mil puntos)
EXTENSION_MIN_KM = 1.0     # trayectoria típica: bajo esto, el recorrido es un circuito (parte y termina igual)


class ConsultaInvalida(ValueError):
    """Pedido del usuario que no se puede responder (filtro faltante, día mal escrito…): la API la
    devuelve como 400 con este mensaje. Los demás errores siguen siendo 500."""


class Cache:
    """Caché de un período y los parquet diarios de donde sale. Guarda también las trayectorias típicas ya
    calculadas (no cambian mientras la caché sea la misma)."""

    def __init__(self, periodo: str, rutas: Rutas):
        self.periodo = periodo
        self.carpeta = rutas.carpeta_cache(periodo)
        self.datos_local = rutas.datos_local
        comando = f"uv run mapa-cache {'--dia' if periodo.count('-') == 2 else '--mes'} {periodo}"
        if not (self.carpeta / "meta.json").exists():
            raise FileNotFoundError(f"No hay caché en {self.carpeta}. Constrúyela con: {comando}")
        self.meta = json.loads((self.carpeta / "meta.json").read_text(encoding="utf-8"))
        if self.meta.get("version") != VERSION_CACHE:
            raise FileNotFoundError(f"La caché de {self.carpeta} es de una versión anterior "
                                    f"({self.meta.get('version')}, se necesita {VERSION_CACHE}). Reconstrúyela con: {comando}")
        self.tam = self.meta["tam"]
        self.tipicas: dict[str, dict | None] = {}
        self._grupo_de: dict[str, int] | None = None

    def grupo(self, patente: str) -> int | None:
        """Archivo agg/grupo=NN donde está la patente (guardado al construir la caché)."""
        if self._grupo_de is None:
            self._grupo_de = dict(pl.read_parquet(self.carpeta / "ranking_patente.parquet",
                                                  columns=["patente", "grupo"]).iter_rows())
        return self._grupo_de.get(patente)

    def agg(self, filtros: dict) -> pl.LazyFrame:
        """Celdas por recorrido × patente × hora × tipo de día, con los filtros dados. Con bus, lee solo el
        archivo de su grupo (1 de 16): ~7 veces más rápido que recorrer todos."""
        g = self.grupo(filtros["patente"]) if filtros.get("patente") else None
        lf = pl.scan_parquet(self.carpeta / "agg" / ("*.parquet" if g is None else f"grupo={g:02d}.parquet"))
        for dim, valor in filtros.items():
            lf = lf.filter(pl.col(dim) == valor)
        return lf

    def archivos_dia(self, d0: dt.date) -> list[Path]:
        """Parquet que cubren el día operativo `d0` (cae en las particiones UTC d0 y d0 + 1)."""
        archivos = []
        for d in (d0, d0 + dt.timedelta(days=1)):
            archivos += sorted(self.datos_local.glob(f"year={d.year}/month={d.month:02d}/day={d.day:02d}/*.parquet"))
        return archivos


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


def _paquete(cabecera: dict, arreglos: dict[str, pl.Series]) -> bytes:
    """[uint32 largo][cabecera JSON][arreglos alineados a 4 bytes], el formato que lee la página. Los bytes
    de cada arreglo salen del búfer Arrow de la serie de Polars, sin pasar por listas de Python."""
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
    meta = json.dumps({**cabecera, "indice": indice}).encode()
    meta += b" " * ((-(4 + len(meta))) % 4)
    return struct.pack("<I", len(meta)) + meta + b"".join(partes)


def celdas(cache: Cache, filtros: dict) -> bytes:
    """Matrices [tipo de día (2), hora (24), celda] de registros, velocidad y velocidad comercial: con ellas
    la página elige y anima la hora sin volver a pedir datos."""
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

    return _paquete({"n_celdas": nc, "buses": int(buses.item())}, {
        "lon": ids.select(((pl.col("ix") + 0.5) * cache.tam).cast(pl.Float32)).to_series(),
        "lat": ids.select(((pl.col("iy") + 0.5) * cache.tam).cast(pl.Float32)).to_series(),
        "n_total": matriz(entero_minimo(m["n_total"])), "n_mov": matriz(entero_minimo(m["n_mov"])),
        "vel": matriz(m["vel"].cast(pl.UInt8)), "vcom": matriz(m["vcom"].cast(pl.UInt8)),
        "scom": matriz(entero_minimo(m["scom"])),
    })


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


# ── Puntos GPS de un día operativo (trayectos y trayectoria típica) ──────────

def _puntos_dia(cache: Cache, d0: dt.date) -> pl.LazyFrame | None:
    """Puntos GPS con posición de todos los buses en un día operativo (04:00 a 04:00 local), con su
    recorrido, servicio y sentido."""
    archivos = cache.archivos_dia(d0)
    if not archivos:
        return None
    return (pl.scan_parquet(archivos, hive_partitioning=False)
            .filter((dia_operativo() == d0) & CON_COORDENADAS)
            .select(plate="plate", ts="timestamp_gps_utc", lon="lon", lat="lat", speed="speed", recorrido=RECORRIDO)
            .with_columns(servicio=SERVICIO, sentido=SENTIDO))


def _viaje(por_bus: bool = False) -> pl.Expr:
    """Número de viaje de cada punto (ordenados por bus y hora): uno nuevo cuando cambia el recorrido,
    hay más de 15 min sin señal, el GPS salta (el tramo implicaría más de SALTO_GPS km/h: sin este corte se
    dibuja una recta que cruza la ciudad) o, con varios buses, cambia el bus."""
    dt_s = pl.col("ts").diff().dt.total_seconds()
    corte = ((pl.col("recorrido") != pl.col("recorrido").shift(1))
             | (dt_s > PAUSA_NUEVO_VIAJE)
             | (km_desde_anterior() * 3600 / dt_s.clip(lower_bound=1) > SALTO_GPS))
    if por_bus:
        corte = corte | (pl.col("plate") != pl.col("plate").shift(1))
    return corte.fill_null(True).cum_sum().cast(pl.Int32) - 1


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
    lf = pl.scan_parquet(cache.carpeta / "dias_bus.parquet")
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
    Puntos GPS del bus en el día operativo, en orden: hora, `t`, lon / lat, km/h, recorrido y viaje. Con
    filtros de servicio / sentido / recorrido, solo los puntos que calzan (los viajes se cortan antes de
    filtrar, con el día completo del bus en orden).
    """
    d0 = fecha(dia)
    puntos = _puntos_dia(cache, d0)
    if puntos is None:
        return pl.DataFrame()
    return (puntos
            .filter(pl.col("plate") == patente)
            .sort("ts")
            .with_columns(viaje=_viaje())
            .filter(_condicion(filtros or {}))
            .select(*_columnas_trayecto(d0), "recorrido", "viaje")
            .collect())


def trayectos(cache: Cache, filtros: dict, dia: str) -> pl.DataFrame:
    """
    Todos los buses que hicieron el servicio / sentido / recorrido filtrado en el día operativo: sus puntos
    en lo filtrado, ordenados por bus y hora, con las mismas columnas que `trayecto` más la patente. Los
    viajes se cortan también al cambiar de bus.
    """
    _con_algun_filtro(filtros)
    d0 = fecha(dia)
    puntos = _puntos_dia(cache, d0)
    if puntos is None:
        return pl.DataFrame()
    df = (puntos
          .filter(_condicion(filtros))
          .sort("plate", "ts")
          .with_columns(viaje=_viaje(por_bus=True))
          .select(*_columnas_trayecto(d0), "recorrido", patente="plate", viaje="viaje")
          .collect())
    if df.height > MAX_PUNTOS_TRAYECTOS:
        raise ConsultaInvalida(f"{df.height:,} puntos: demasiados para dibujar. Filtra por sentido o recorrido.")
    return df


# ── Trayectoria típica de cada recorrido ─────────────────────────────────────

def trayectoria_tipica(cache: Cache, recorrido: str) -> dict | None:
    """
    Un viaje real y representativo del recorrido, para dibujarlo unido como línea: se elige un bus-día
    con registros cercanos a la mediana del recorrido y, de sus viajes de ese recorrido, el que va más lejos
    de punta a punta (de terminal a terminal). No el de más puntos: ese suele ser una ida y vuelta con el
    letrero sin cambiar, o un viaje con una larga espera. En un circuito (parte y termina en el mismo lugar)
    todos van ~0 km de punta a punta, y ahí sí se elige el de más puntos.
    Un viaje nuevo empieza cuando cambia el recorrido asignado, hay más de 15 min sin señal o el GPS salta.
    """
    if recorrido in cache.tipicas:
        return cache.tipicas[recorrido]
    candidatos = (pl.scan_parquet(cache.carpeta / "dias_bus.parquet")
                  .filter((pl.col("recorrido") == recorrido) & (pl.col("n") >= 20))
                  .with_columns(dist=(pl.col("n") - pl.col("n").median()).abs())
                  .sort("dist").head(3).select("patente", "dia").collect())
    hora = lambda c: pl.col(c).dt.convert_time_zone(ZONA).dt.strftime("%H:%M")  # noqa: E731
    resultado = None
    for patente, d0 in candidatos.iter_rows():
        puntos = _puntos_dia(cache, d0)
        if puntos is None:
            continue
        mejor = (puntos
                 .filter(pl.col("plate") == patente)
                 .sort("ts")
                 .with_columns(viaje=_viaje())
                 .filter(pl.col("recorrido") == recorrido)
                 .group_by("viaje")
                 .agg(n=pl.len(), inicio=pl.col("ts").first(), fin=pl.col("ts").last(),
                      extension=km_entre(pl.col("lon").first(), pl.col("lat").first(),
                                         pl.col("lon").last(), pl.col("lat").last()),
                      camino=pl.concat_list(pl.col("lon").cast(pl.Float64).round(5),
                                            pl.col("lat").cast(pl.Float64).round(5)))
                 .filter(pl.col("n") >= 10)
                 .sort(pl.when(pl.col("extension").max() >= EXTENSION_MIN_KM).then(pl.col("extension"))
                       .otherwise(pl.col("n").cast(pl.Float64)), "n", descending=True)
                 .head(1)
                 .select(pl.lit(recorrido).alias("recorrido"), pl.lit(patente).alias("patente"),
                         pl.lit(d0.isoformat()).alias("dia"), hora("inicio").alias("inicio"),
                         hora("fin").alias("fin"), "n", "camino")
                 .collect())
        if mejor.height:
            resultado = mejor.row(0, named=True)
            break  # con el primer bus-día que tenga un viaje basta
    cache.tipicas[recorrido] = resultado
    return resultado


def tipicas(cache: Cache, filtros: dict) -> pl.DataFrame:
    """Trayectoria típica de los recorridos que se ven en la vista Recorridos (los 5 con más registros)."""
    if not filtros:
        raise ConsultaInvalida("La trayectoria típica requiere al menos un filtro")
    principales = (_resumen_por_recorrido(cache, filtros).head(MAX_RECORRIDOS_COLOR)
                   .select("id").collect()["id"].to_list())
    filas = [t for t in (trayectoria_tipica(cache, r) for r in principales) if t]
    return pl.DataFrame(filas, schema={"recorrido": pl.String, "patente": pl.String, "dia": pl.String,
                                       "inicio": pl.String, "fin": pl.String, "n": pl.UInt32,
                                       "camino": pl.List(pl.List(pl.Float64))})
