"""
Construcción de la caché del mapa de velocidad (Polars) desde los parquet diarios de posiciones de buses
RED (`datos_local/year=YYYY/month=MM/day=DD/*.parquet`). En AWS corre como tarea programada tras la
actualización diaria del lakehouse; la API solo lee la caché resultante.

Arma `cache_polars_<periodo>/`:
    agg/grupo=NN.parquet   celdas de ~100 m por recorrido × bus × hora × tipo de día (sumas para métricas)
    celdas_global.parquet  lo mismo sin recorrido ni bus (vista sin filtros)
    ranking_<dim>.parquet  métricas por servicio, sentido, recorrido y patente
    dias_bus.parquet       registros con posición por bus × día operativo × recorrido
    meta.json              tamaño de celda, días por tipo y versión

Uso:
    uv run mapa-cache                    # mes 2025-09
    uv run mapa-cache --mes 2025-10
    uv run mapa-cache --dia 2025-09-15
    uv run mapa-cache --rehacer          # regenerar aunque exista
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import time
from pathlib import Path

import polars as pl

from .config import PERIODO_POR_DEFECTO, Rutas
from .esquema import (
    CON_COORDENADAS,
    DIMENSIONES,
    ES_TS,
    PAUSA_NUEVO_VIAJE,
    RECORRIDO,
    SALTO_GPS,
    SENTIDO,
    SERVICIO,
    SUMAS,
    dia_operativo,
    hora_local,
    km_desde_anterior,
)

# 2: sin columna `chofer` ni `op_bus.parquet`; días por bus solo con registros con posición
# 3: `ranking_patente.parquet` con el grupo de cada patente (la API lee solo ese archivo al filtrar por bus)
VERSION = 3

# Caja aproximada del Gran Santiago (descarta GPS fuera de la ciudad)
LON_MIN, LON_MAX = -71.0, -70.3
LAT_MIN, LAT_MAX = -33.8, -33.1

VEL_MAX = 100       # km/h — lecturas sobre esto se consideran ruido
VEL_DETENIDO = 3    # km/h — bajo esto el bus se considera detenido
N_GRUPOS = 16       # grupos de patentes en que se divide la caché (más grupos = menos memoria)
TAM_CELDA = 0.001   # grados (≈ 100 m)

# Velocidad comercial en ruta = km recorridos / tiempo en el recorrido, incluidas las detenciones normales
# (paraderos, semáforos, congestión). No cuenta huecos sin señal de más de PAUSA_NUEVO_VIAJE, ni:
ESPERA_TERMINAL = 5 * 60  # s — detenciones seguidas más largas que esto (terminal, regulación)
# … ni los saltos de GPS (esquema.SALTO_GPS), ni el tramo en que cambia el recorrido asignado, ni los
# códigos TS (fuera de servicio).

# Feriados nacionales (se tratan como fin de semana), de septiembre de 2025 a diciembre de 2026.
# Para procesar otros períodos hay que agregar los suyos.
FERIADOS = [
    "2025-09-18", "2025-09-19", "2025-10-12", "2025-10-31", "2025-11-01",
    "2025-11-16", "2025-12-08", "2025-12-14", "2025-12-25",
    "2026-01-01", "2026-04-03", "2026-04-04", "2026-05-01", "2026-05-21",
    "2026-06-21", "2026-06-29", "2026-07-16", "2026-08-15", "2026-09-18",
    "2026-09-19", "2026-10-12", "2026-10-31", "2026-11-01", "2026-12-08", "2026-12-25",
]
# `implode` deja la lista como un solo valor: así `is_in` compara contra la lista completa
FERIADOS_FECHAS = pl.lit(pl.Series([dt.date.fromisoformat(f) for f in FERIADOS], dtype=pl.Date)).implode()


def es_fin_de_semana(fecha: pl.Expr) -> pl.Expr:
    """Sábado, domingo o feriado."""
    return (fecha.dt.weekday() >= 6) | fecha.is_in(FERIADOS_FECHAS)


TIPO_DIA = pl.when(es_fin_de_semana(hora_local().dt.date())).then(1).otherwise(0).cast(pl.Int8)


def grupo_patente(expr: pl.Expr) -> pl.Expr:
    """Grupo de la patente (0..N_GRUPOS-1). Solo se calcula al construir: `hash` no es estable entre versiones
    de Polars, así que el grupo de cada patente queda guardado en `ranking_patente.parquet` y la API lo lee
    de ahí (no lo vuelve a calcular)."""
    return (expr.hash(seed=0) % N_GRUPOS).cast(pl.Int32)


def con_tramos_en_ruta(lf: pl.LazyFrame) -> pl.LazyFrame:
    """
    Agrega a cada registro el tramo desde el registro anterior del mismo bus:
    `dx_km`, `dt_s` y `en_ruta` (si ese tramo cuenta para la velocidad comercial en ruta).
    Requiere la columna `recorrido` y ordena por bus y hora.
    """
    return (
        lf.sort("plate", "timestamp_gps_utc")
        .with_columns(
            dt_s=pl.col("timestamp_gps_utc").diff().over("plate").dt.total_seconds(),
            dx_km=km_desde_anterior(por="plate"),
            mismo_rec=pl.col("recorrido") == pl.col("recorrido").shift(1).over("plate"),
            parado=pl.col("speed").fill_null(0) < VEL_DETENIDO,
            es_ts=ES_TS,
        )
        # racha = registros seguidos en el mismo estado (detenido / en movimiento)
        .with_columns(racha=(pl.col("parado") != pl.col("parado").shift(1).over("plate"))
                            .fill_null(True).cum_sum().over("plate"))
        .with_columns(dur_racha=pl.col("dt_s").fill_null(0).sum().over("plate", "racha"))
        .with_columns(en_ruta=(
            pl.col("dt_s").is_between(1, PAUSA_NUEVO_VIAJE)
            & pl.col("mismo_rec") & ~pl.col("es_ts")
            & (pl.col("dx_km") / pl.col("dt_s") * 3600 <= SALTO_GPS)
            & ~(pl.col("parado") & (pl.col("dur_racha") > ESPERA_TERMINAL))
        ).fill_null(False))
    )


def archivos_parquet(rutas: Rutas, mes: str, dia: str | None = None) -> tuple[list[Path], str]:
    """Devuelve (archivos parquet, etiqueta del período): un día si se da, si no el mes."""
    if dia:
        anio, m, d = dia.split("-")
        return sorted(rutas.datos_local.glob(f"year={anio}/month={m}/day={d}/*.parquet")), dia
    anio, m = mes.split("-")
    return sorted(rutas.datos_local.glob(f"year={anio}/month={m}/*/*.parquet")), mes


def consulta_agg(del_grupo: pl.LazyFrame, tam: float) -> pl.LazyFrame:
    """Celdas por recorrido × patente × hora × tipo de día. Los tramos en ruta se calculan antes de filtrar,
    con todos los registros del bus en orden."""
    return (
        con_tramos_en_ruta(
            del_grupo.select("plate", "timestamp_gps_utc", "bus_route_assigned", "speed", "lon", "lat")
            .with_columns(recorrido=RECORRIDO))
        .filter(pl.col("lon").is_between(LON_MIN, LON_MAX)
                & pl.col("lat").is_between(LAT_MIN, LAT_MAX)
                & pl.col("speed").is_between(0, VEL_MAX))
        .with_columns(
            servicio=SERVICIO,
            sentido=SENTIDO,
            patente=pl.col("plate"),
            # en Float64 (división exacta): en Float32 algunos puntos del borde caen en la celda vecina
            ix=(pl.col("lon").cast(pl.Float64) / tam).floor().cast(pl.Int32),
            iy=(pl.col("lat").cast(pl.Float64) / tam).floor().cast(pl.Int32),
            tipo_dia=TIPO_DIA,
            hora=hora_local().dt.hour().cast(pl.Int8),
            mov=pl.col("speed") >= VEL_DETENIDO,
        )
        .group_by("recorrido", "servicio", "sentido", "patente", "ix", "iy", "tipo_dia", "hora")
        .agg(
            n_total=pl.len().cast(pl.Int32),
            n_mov=pl.col("mov").sum().cast(pl.Int32),
            suma_vel=pl.when(pl.col("mov")).then(pl.col("speed")).otherwise(0).sum().cast(pl.Float32),
            # tramos en ruta que terminan en esta celda/hora (para la velocidad comercial)
            km_ruta=pl.when(pl.col("en_ruta")).then(pl.col("dx_km")).otherwise(0).sum().cast(pl.Float32),
            s_ruta=pl.when(pl.col("en_ruta")).then(pl.col("dt_s")).otherwise(0).sum().cast(pl.Float32),
        )
    )


def consulta_dias_bus(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Registros con posición por bus × día operativo × recorrido (lista de días del modo Trayecto). Solo
    con posición: son los que `/trayecto` puede dibujar, así no se ofrecen días que saldrían vacíos."""
    return (lf.filter(CON_COORDENADAS)
              .with_columns(recorrido=RECORRIDO)
              .group_by(patente=pl.col("plate"), dia=dia_operativo(), recorrido=pl.col("recorrido"))
              .agg(n=pl.len().cast(pl.Int32))
              .with_columns(servicio=SERVICIO, sentido=SENTIDO))


def construir_cache(archivos: list[Path], carpeta: Path, tam: float = TAM_CELDA) -> None:
    """Arma la caché en una carpeta temporal y la reemplaza al final (si falla, la anterior queda intacta)."""
    print(f"Construyendo caché Polars desde {len(archivos)} archivos en {N_GRUPOS} grupos de patentes…")
    t0 = time.time()
    tmp = carpeta.with_name(carpeta.name + "_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "agg").mkdir(parents=True)
    (tmp / "dias").mkdir()

    crudo = pl.scan_parquet(archivos, hive_partitioning=False)
    opciones_parquet = dict(row_group_size=100_000, statistics=True, compression="zstd")

    for g in range(N_GRUPOS):
        del_grupo = crudo.filter(grupo_patente(pl.col("plate")) == g)
        # Las dos salidas van directo a disco en una sola ejecución (comparten la lectura del grupo).
        # Orden por recorrido: como servicio es su prefijo, filtrar por cualquiera de los dos salta row groups.
        pl.collect_all([
            consulta_agg(del_grupo, tam).sort("recorrido", "patente")
            .sink_parquet(tmp / "agg" / f"grupo={g:02d}.parquet", lazy=True, **opciones_parquet),
            consulta_dias_bus(del_grupo).sink_parquet(tmp / "dias" / f"grupo={g:02d}.parquet", lazy=True),
        ], engine="streaming")
        print(f"  grupo {g + 1}/{N_GRUPOS} ({time.time() - t0:.0f} s)")

    # Resúmenes, todos lazy sobre lo ya escrito. Como cada patente está en un solo grupo, contar buses
    # distintos sobre todos los grupos es exacto.
    agg_todo = pl.scan_parquet(tmp / "agg" / "*.parquet")
    pl.collect_all([
        agg_todo.group_by("ix", "iy", "tipo_dia", "hora").agg(pl.col(SUMAS).sum())
                .sink_parquet(tmp / "celdas_global.parquet", lazy=True),
        *[agg_todo.group_by(dim).agg(pl.col(SUMAS).sum(), buses=pl.col("patente").n_unique())
                  # cada patente está en un solo archivo agg/grupo=NN: la API filtra por bus leyendo solo ese
                  .with_columns(**({"grupo": grupo_patente(pl.col("patente"))} if dim == "patente" else {}))
                  .sink_parquet(tmp / f"ranking_{dim}.parquet", lazy=True) for dim in DIMENSIONES],
        pl.scan_parquet(tmp / "dias" / "*.parquet").sink_parquet(tmp / "dias_bus.parquet", lazy=True),
    ], engine="streaming")
    shutil.rmtree(tmp / "dias")

    filas_total = agg_todo.select(pl.len()).collect().item()
    por_tipo = (pl.scan_parquet(tmp / "dias_bus.parquet").select("dia").unique()
                  .with_columns(tipo=pl.when(es_fin_de_semana(pl.col("dia"))).then(1).otherwise(0))
                  .group_by("tipo").len().collect())
    meta = {"version": VERSION, "tam": tam, "n_grupos": N_GRUPOS, "filas": filas_total,
            "dias": {str(t): int(n) for t, n in por_tipo.iter_rows()}}
    (tmp / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    shutil.rmtree(carpeta, ignore_errors=True)
    tmp.rename(carpeta)
    print(f"Caché lista: {filas_total:,} filas agregadas en {time.time() - t0:.0f} s, en {carpeta.name}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mes", default=PERIODO_POR_DEFECTO, help="Mes a procesar (YYYY-MM)")
    ap.add_argument("--dia", help="Procesar solo un día (YYYY-MM-DD); tiene prioridad sobre --mes")
    ap.add_argument("--tam", type=float, default=TAM_CELDA, help="Tamaño de celda en grados (0.001 ≈ 100 m)")
    ap.add_argument("--rehacer", action="store_true", help="Regenerar la caché aunque exista")
    args = ap.parse_args(argv)

    rutas = Rutas.desde_entorno()
    archivos, periodo = archivos_parquet(rutas, args.mes, args.dia)
    if not archivos:
        raise SystemExit(f"No hay parquet en {rutas.datos_local} para {periodo}")
    carpeta = rutas.carpeta_cache(periodo)
    meta = carpeta / "meta.json"
    vigente = meta.exists() and json.loads(meta.read_text(encoding="utf-8")).get("version") == VERSION
    if args.rehacer or not vigente:
        construir_cache(archivos, carpeta, args.tam)
    else:
        print(f"La caché ya existe: {carpeta}")


if __name__ == "__main__":
    main()
