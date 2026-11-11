"""
Construcción de la caché del mapa de velocidad (Polars) desde los parquet diarios de posiciones de buses
RED (`datos_local/year=YYYY/month=MM/day=DD/*.parquet`). En AWS corre como tarea programada tras la
actualización diaria del lakehouse. La API solo lee la caché que sale de aquí, nunca los parquet diarios.

Arma `cache_polars_<periodo>/`:
    agg/grupo=NN.parquet     celdas de ~100 m por recorrido × bus × hora × tipo de día (sumas para métricas)
    celdas_global.parquet    lo mismo sin recorrido ni bus (vista sin filtros)
    ranking_<dim>.parquet    métricas por servicio, sentido, recorrido y patente
    dias_bus.parquet         registros con posición por bus × día operativo × recorrido
    trayectos/dia=AAAA-MM-DD.parquet
                             los puntos GPS de cada día operativo, con su número de viaje
    tipicas.parquet          la trayectoria típica (un viaje real) de cada recorrido
    meta.json                tamaño de celda, días por tipo y versión

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
import pyarrow.parquet as pq

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
    ZONA,
    dia_operativo,
    hora_local,
    km_desde_anterior,
    km_entre,
    viaje,
)

# 2: sin columna `chofer` ni `op_bus.parquet`; días por bus solo con registros con posición
# 3: `ranking_patente.parquet` con el grupo de cada patente (la API lee solo ese archivo al filtrar por bus)
# 4: se agregan `trayectos/` y `tipicas.parquet`, y la API deja de leer los parquet diarios
VERSION = 4

# Caja aproximada del Gran Santiago (descarta GPS fuera de la ciudad)
LON_MIN, LON_MAX = -71.0, -70.3
LAT_MIN, LAT_MAX = -33.8, -33.1

VEL_MAX = 100       # km/h — lecturas sobre esto se consideran ruido
VEL_DETENIDO = 3    # km/h — bajo esto el bus se considera detenido
N_GRUPOS = 16       # grupos de patentes en que se divide la caché (más grupos = menos memoria)
TAM_CELDA = 0.001   # grados (≈ 100 m)
# Filas por row group en trayectos/. Un recorrido en un día tiene entre 15 y 60 mil puntos, así que queda en
# unos pocos y la API lee solo esos.
FILAS_GRUPO_TRAYECTOS = 20_000
# Trayectoria típica: si ningún viaje llega a esta distancia de punta a punta, el recorrido es un circuito
# (parte y termina en el mismo lugar)
EXTENSION_MIN_KM = 1.0
# Columnas de tipicas.parquet (sirve para escribirlo vacío cuando no hay datos)
ESQUEMA_TIPICAS = {"recorrido": pl.String, "patente": pl.String, "dia": pl.String, "inicio": pl.String,
                   "fin": pl.String, "n": pl.UInt32, "camino": pl.List(pl.List(pl.Float64))}

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


def puntos_dia(crudo: pl.LazyFrame, d0: dt.date) -> pl.LazyFrame:
    """
    Los puntos GPS de todos los buses en el día operativo `d0`, listos para el modo Trayecto.

    Son los mismos puntos que cuenta `dias_bus` (los que tienen posición). Cada uno lleva su número de viaje,
    calculado con el día completo del bus. Quedan ordenados por recorrido, bus y hora, para que la API lea solo
    la parte del archivo que necesita.
    """
    # El día operativo (de 04:00 a 04:00) cae entre dos particiones UTC: la de ese día y la del siguiente.
    # Filtrar primero por ese rango le permite a Polars saltarse los demás archivos; si no, leería el mes
    # entero por cada día.
    desde = dt.datetime.combine(d0, dt.time(), tzinfo=dt.timezone.utc)
    en_sus_particiones = pl.col("timestamp_gps_utc").is_between(desde, desde + dt.timedelta(days=2), closed="left")
    return (crudo
            .filter(en_sus_particiones & (dia_operativo() == d0) & CON_COORDENADAS)
            .select(patente="plate", ts="timestamp_gps_utc", lon="lon", lat="lat", speed="speed", recorrido=RECORRIDO)
            .with_columns(servicio=SERVICIO, sentido=SENTIDO)
            .sort("patente", "ts")               # en orden de hora para numerar los viajes…
            .with_columns(viaje=viaje())
            .sort("recorrido", "patente", "ts")  # …y por recorrido para guardarlos
            .select("servicio", "sentido", "recorrido", "patente", "ts", "lon", "lat", "speed", "viaje"))


def escribir_trayectos(puntos: pl.DataFrame, archivo: Path) -> None:
    """
    Guarda los puntos de un día con pyarrow en vez de Polars, porque permite elegir cómo se codifica cada
    columna. Así el archivo pesa la mitad (un día de septiembre de 2025: 53 MB en vez de 95) y Polars lo lee
    igual de rápido.
    """
    pq.write_table(
        puntos.to_arrow(), archivo, compression="zstd", row_group_size=FILAS_GRUPO_TRAYECTOS,
        # Los textos se repiten mucho: van como diccionario
        use_dictionary=["servicio", "sentido", "recorrido", "patente"],
        column_encoding={
            # La hora y el viaje avanzan de a poco: se guarda cuánto cambian de un punto al siguiente
            "ts": "DELTA_BINARY_PACKED", "viaje": "DELTA_BINARY_PACKED",
            # Los decimales comprimen mucho mejor con sus bytes separados
            "lon": "BYTE_STREAM_SPLIT", "lat": "BYTE_STREAM_SPLIT", "speed": "BYTE_STREAM_SPLIT",
        })


# ── Trayectoria típica de cada recorrido ─────────────────────────────────────
# Es un viaje real que sirve de muestra del recorrido. Se toma un bus-día "normal" (con una cantidad de
# registros parecida a la mediana del recorrido) y, de sus viajes en ese recorrido, el que llega más lejos de
# punta a punta, o sea, de terminal a terminal. No sirve el de más puntos: suele ser una ida y vuelta con el
# letrero sin cambiar, o un viaje con una espera larga. La excepción son los circuitos, que parten y terminan
# en el mismo lugar: ahí todos los viajes van ~0 km de punta a punta, y se elige el de más puntos.

def candidatos_tipica(dias_bus: pl.LazyFrame) -> pl.LazyFrame:
    """
    Hasta 3 bus-día por recorrido: los de cantidad de registros más cercana a la mediana del recorrido (con
    20 o más). `rango` da el orden de preferencia; se usa el primero que tenga un viaje de 10 puntos o más.
    """
    distancia_a_la_mediana = (pl.col("n") - pl.col("n").median().over("recorrido")).abs()
    return (dias_bus
            .filter(pl.col("n") >= 20)
            .with_columns(dist=distancia_a_la_mediana)
            .sort("recorrido", "dist", "patente", "dia")  # patente y día solo desempatan: siempre sale lo mismo
            .with_columns(rango=pl.int_range(pl.len()).over("recorrido"))
            .filter(pl.col("rango") < 3)
            .select("recorrido", "patente", "dia", "rango"))


def viajes_candidatos(puntos: pl.DataFrame, candidatos: pl.DataFrame) -> pl.DataFrame:
    """
    Resume cada viaje de los candidatos de un día en su recorrido: cuántos puntos tiene, a qué hora empezó y
    terminó, cuántos km hay de punta a punta y el camino completo. Se calcula al tiro (no queda lazy) para que
    los puntos del día se puedan soltar de la memoria antes de pasar al siguiente.
    """
    de_punta_a_punta = km_entre(pl.col("lon").first(), pl.col("lat").first(),
                                pl.col("lon").last(), pl.col("lat").last())
    camino = pl.concat_list(pl.col("lon").cast(pl.Float64).round(5), pl.col("lat").cast(pl.Float64).round(5))
    return (puntos.lazy()
            .join(candidatos.lazy(), on=["recorrido", "patente"])
            .sort("recorrido", "patente", "ts")  # el join puede desordenar los puntos, y el camino va en orden
            .group_by("recorrido", "rango", "patente", "dia", "viaje")
            .agg(n=pl.len(), inicio=pl.col("ts").first(), fin=pl.col("ts").last(),
                 extension=de_punta_a_punta, camino=camino)
            .collect())


def elegir_tipicas(viajes: pl.LazyFrame) -> pl.LazyFrame:
    """La trayectoria típica de cada recorrido: el mejor viaje del primer candidato que tenga uno de 10 puntos
    o más."""
    # Si ningún viaje del candidato llega a 1 km de punta a punta, el recorrido es un circuito: los viajes se
    # comparan por cantidad de puntos en vez de por distancia
    es_circuito = pl.col("extension").max().over("recorrido", "rango") < EXTENSION_MIN_KM
    puntaje = pl.when(es_circuito).then(pl.col("n").cast(pl.Float64)).otherwise("extension")
    return (viajes
            .filter(pl.col("n") >= 10)
            .with_columns(puntaje=puntaje)
            # Primero el candidato preferido y, dentro de él, el viaje de más puntaje (si empatan, el de más
            # puntos y luego el más temprano)
            .sort(["recorrido", "rango", "puntaje", "n", "inicio"], descending=[False, False, True, True, False])
            .unique("recorrido", keep="first", maintain_order=True)
            .select("recorrido", "patente", pl.col("dia").cast(pl.String), _hora_local("inicio"),
                    _hora_local("fin"), pl.col("n").cast(pl.UInt32), "camino"))


def _hora_local(columna: str) -> pl.Expr:
    """La hora de Chile como texto HH:MM."""
    return pl.col(columna).dt.convert_time_zone(ZONA).dt.strftime("%H:%M")


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

    # Los trayectos se guardan día por día. Mientras cada día está en memoria, se resumen también los viajes de
    # sus candidatos a trayectoria típica, para no tener que volver a leerlo después.
    dias_bus = pl.scan_parquet(tmp / "dias_bus.parquet")
    candidatos = candidatos_tipica(dias_bus).collect()
    (tmp / "trayectos").mkdir()
    viajes = []
    for d0 in dias_bus.select(pl.col("dia").unique().sort()).collect().to_series():
        # Este paso sí trae el día a memoria: pyarrow escribe desde memoria (con un sink lazy de Polars no se
        # puede elegir la codificación de cada columna), y de paso sirve para los candidatos del día
        puntos = puntos_dia(crudo, d0).collect()
        escribir_trayectos(puntos, tmp / "trayectos" / f"dia={d0}.parquet")
        viajes.append(viajes_candidatos(puntos, candidatos.filter(pl.col("dia") == d0)))
        print(f"  trayectos del {d0}: {puntos.height:,} puntos ({time.time() - t0:.0f} s)")
    tipicas = elegir_tipicas(pl.concat(viajes).lazy()) if viajes else pl.LazyFrame(schema=ESQUEMA_TIPICAS)
    tipicas.sink_parquet(tmp / "tipicas.parquet")

    filas_total = agg_todo.select(pl.len()).collect().item()
    por_tipo = (dias_bus.select("dia").unique()
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
