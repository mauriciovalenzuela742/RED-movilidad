"""Datos SIMULADOS para mostrar el mapa de velocidad (`mapa/`) sin acceso al lakehouse.

    python -m pulso.demo_mapa --dias 2026-10-07 2026-10-10

No son datos reales. Cada bus recorre el trazado real de su servicio (GTFS vigente, `data/external/gtfs/feed`)
con las salidas que dan las frecuencias del GTFS y los tiempos de viaje entre paraderos de su
`stop_times.txt`, más ruido aleatorio en la velocidad (semilla fija). Sirve para ver el mapa de calor y la
reproducción de trayectos funcionando; las velocidades que muestra son las del itinerario planificado, no
las observadas. Las patentes son ficticias con formato chileno ("BJFV-17") y el operador "SIMULADO (GTFS)".

Escribe `mapa/datos_demo/year=YYYY/month=MM/day=DD/datos-N.parquet` (particiones UTC, el mismo esquema que
`pulso.descarga_mapa`), separado de `mapa/datos_local/` para no mezclarlo nunca con datos reales.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

RAIZ = Path(__file__).resolve().parent.parent
GTFS = RAIZ / "data" / "external" / "gtfs" / "feed"
DESTINO = RAIZ / "mapa" / "datos_demo"
SANTIAGO = ZoneInfo("America/Santiago")
CADA_S = 30          # un punto GPS cada 30 s
DESCANSO_S = 6 * 60  # descanso mínimo en el terminal antes de reutilizar un bus
INICIO_DIA_S = 4 * 3600  # día operativo desde las 04:00 (como en mapa/)
ESQUEMA = pa.schema([("timestamp_gps_utc", pa.timestamp("us")), ("plate", pa.string()),
                     ("bus_route_assigned", pa.string()), ("operator_name", pa.string()),
                     ("operator_number", pa.int32()), ("speed", pa.float32()),
                     ("lon", pa.float64()), ("lat", pa.float64())])


_LETRAS = "BCDFGHJKLPRSTVWXYZ"  # consonantes de las patentes chilenas actuales
_N_PATENTES = len(_LETRAS) ** 4 * 100


def patente_ficticia(n: int) -> str:
    """Patente FICTICIA con el formato del lakehouse ("BJFV-17"), única para cada n (biyección: 7919 es primo
    con el total de combinaciones). Puede coincidir por azar con una patente real: los datos son simulados."""
    k = (n * 7919 + 104_729) % _N_PATENTES
    k, num = divmod(k, 100)
    letras = ""
    for _ in range(4):
        k, i = divmod(k, len(_LETRAS))
        letras += _LETRAS[i]
    return f"{letras}-{num:02d}"


def _seg(hhmmss: pd.Series) -> np.ndarray:
    p = hhmmss.str.split(":", expand=True).astype(int)
    return (p[0] * 3600 + p[1] * 60 + p[2]).to_numpy()


def _metros(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Distancia acumulada (m) a lo largo de una polilínea (equirectangular; basta a escala urbana)."""
    dx = np.diff(lon) * np.cos(np.radians(lat[:-1])) * 111_320
    dy = np.diff(lat) * 110_540
    return np.concatenate([[0.0], np.cumsum(np.hypot(dx, dy))])


def cargar_gtfs():
    trips = pd.read_csv(GTFS / "trips.txt", dtype=str)
    routes = pd.read_csv(GTFS / "routes.txt", dtype=str)[["route_id", "route_short_name"]]
    freq = pd.read_csv(GTFS / "frequencies.txt", dtype={"trip_id": str})
    st = pd.read_csv(GTFS / "stop_times.txt", dtype=str,
                     usecols=["trip_id", "arrival_time", "stop_id", "stop_sequence"])
    st = st[st.trip_id.isin(freq.trip_id)].copy()
    st["t"] = _seg(st.arrival_time)
    st["stop_sequence"] = st.stop_sequence.astype(int)
    stops = pd.read_csv(GTFS / "stops.txt", dtype={"stop_id": str})[["stop_id", "stop_lat", "stop_lon"]]
    st = st.merge(stops, on="stop_id").sort_values(["trip_id", "stop_sequence"])
    shapes = pd.read_csv(GTFS / "shapes.txt", dtype={"shape_id": str}).sort_values(
        ["shape_id", "shape_pt_sequence"])
    trips = trips.merge(routes, on="route_id")
    return trips, freq, st, shapes


def perfil_viaje(paradas: pd.DataFrame, forma: pd.DataFrame):
    """(t relativo de cada vértice del trazado, distancia, lon, lat), interpolando los tiempos de los
    paraderos sobre la forma."""
    lon, lat = forma.shape_pt_lon.to_numpy(), forma.shape_pt_lat.to_numpy()
    dist = _metros(lon, lat)
    # cada paradero se proyecta al vértice más cercano, sin retroceder respecto del anterior
    idx, desde = [], 0
    for plon, plat in zip(paradas.stop_lon.to_numpy(), paradas.stop_lat.to_numpy()):
        d2 = (lon[desde:] - plon) ** 2 + (lat[desde:] - plat) ** 2
        desde = desde + int(np.argmin(d2[: max(len(d2) // 3, 200)]))
        idx.append(desde)
    t_par = paradas.t.to_numpy() - paradas.t.to_numpy()[0]
    d_par = dist[idx]
    ok = np.concatenate([[True], np.diff(d_par) > 0])  # descarta paraderos proyectados al mismo punto
    t_vert = np.interp(dist, d_par[ok], t_par[ok])
    return t_vert, dist, lon, lat


def simular_dia(dia: date, trips, freq, st, shapes, rng) -> pd.DataFrame:
    servicio = "L" if dia.weekday() < 5 else ("S" if dia.weekday() == 5 else "D")
    viajes = trips[trips.service_id == servicio]
    f = freq.merge(viajes[["trip_id", "route_short_name", "direction_id", "shape_id"]], on="trip_id")
    f["ini"], f["fin"] = _seg(f.start_time), _seg(f.end_time)
    paradas_por_viaje = dict(tuple(st[st.trip_id.isin(f.trip_id)].groupby("trip_id")))
    formas = dict(tuple(shapes[shapes.shape_id.isin(f.shape_id)].groupby("shape_id")))
    perfiles = {}
    for tid, sid in f[["trip_id", "shape_id"]].drop_duplicates().itertuples(index=False):
        if tid in paradas_por_viaje and sid in formas and len(paradas_por_viaje[tid]) >= 2:
            perfiles[tid] = perfil_viaje(paradas_por_viaje[tid], formas[sid])

    # salidas de cada servicio y sentido según las frecuencias
    salidas = []
    for r in f.itertuples(index=False):
        if r.trip_id in perfiles:
            for t0 in range(int(r.ini), int(r.fin), int(r.headway_secs)):
                salidas.append((r.route_short_name, int(r.direction_id), t0, r.trip_id))
    salidas.sort(key=lambda s: s[2])

    # buses: se reutiliza uno que terminó en ese terminal (llegó en el sentido contrario) y ya descansó
    libres = defaultdict(list)  # (servicio, sentido en que puede salir) -> [(hora en que queda libre, patente)]
    n_buses, filas = 0, []
    for serv, sentido, t0, tid in salidas:
        cola = libres[(serv, sentido)]
        cola.sort()
        if cola and cola[0][0] <= t0:
            _, patente = cola.pop(0)
        else:
            n_buses += 1
            patente = patente_ficticia(n_buses)
        t_vert, dist, lon, lat = perfiles[tid]
        t_v = t_vert * rng.uniform(0.85, 1.15)  # tráfico de ese viaje
        ts = np.arange(0, t_v[-1], CADA_S, dtype=float)
        if len(ts) < 2:
            continue
        d = np.interp(ts, t_v, dist)
        vel = np.clip(np.gradient(d, ts) * 3.6 * rng.uniform(0.8, 1.2, len(ts)), 0, 90)
        vel[rng.random(len(ts)) < 0.08] = 0  # detenciones en paraderos y semáforos
        recorrido = f"{serv if serv[0].isalpha() else 'T' + serv} 00{'I' if sentido == 0 else 'R'}"
        filas.append(pd.DataFrame({
            "t_local": t0 + ts, "plate": patente, "bus_route_assigned": recorrido, "speed": vel,
            "lon": np.interp(d, dist, lon) + rng.normal(0, 4e-5, len(ts)),
            "lat": np.interp(d, dist, lat) + rng.normal(0, 4e-5, len(ts))}))
        libres[(serv, 1 - sentido)].append((t0 + int(t_v[-1]) + DESCANSO_S, patente))

    df = pd.concat(filas, ignore_index=True)
    df = df[df.t_local >= INICIO_DIA_S]  # solo el día operativo (desde las 04:00)
    medianoche = pd.Timestamp(datetime(dia.year, dia.month, dia.day), tz=SANTIAGO)
    df["timestamp_gps_utc"] = ((medianoche + pd.to_timedelta(df.t_local, unit="s"))
                               .dt.tz_convert("UTC").dt.tz_localize(None).dt.floor("s"))
    df["operator_name"] = "SIMULADO (GTFS)"
    df["operator_number"] = 0
    print(f"{dia} ({servicio}): {len(salidas):,} salidas, {n_buses:,} buses, {len(df):,} puntos")
    return df[ESQUEMA.names]


def escribir(df: pd.DataFrame, destino: Path) -> None:
    """Escribe las filas en las particiones UTC que correspondan (un archivo nuevo por partición)."""
    for dia_utc, parte in df.groupby(df.timestamp_gps_utc.dt.date):
        carpeta = Path(destino) / f"year={dia_utc:%Y}" / f"month={dia_utc:%m}" / f"day={dia_utc:%d}"
        carpeta.mkdir(parents=True, exist_ok=True)
        n = len(list(carpeta.glob("*.parquet")))
        tabla = pa.Table.from_pandas(parte.sort_values(["plate", "timestamp_gps_utc"]),
                                     preserve_index=False).cast(ESQUEMA)
        pq.write_table(tabla, carpeta / f"datos-{n}.parquet", compression="zstd")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", nargs="+", type=date.fromisoformat,
                    default=[date(2026, 10, 7), date(2026, 10, 10)], help="Días operativos (hora local)")
    ap.add_argument("--destino", type=Path, default=DESTINO)
    ap.add_argument("--semilla", type=int, default=2026)
    args = ap.parse_args(argv)
    if any(Path(args.destino).rglob("*.parquet")):
        raise SystemExit(f"{args.destino} ya tiene datos; bórralo para regenerar")
    rng = np.random.default_rng(args.semilla)
    gtfs = cargar_gtfs()
    for dia in args.dias:
        escribir(simular_dia(dia, *gtfs, rng), args.destino)
    print(f"Listo (SIMULADO). Datos en {args.destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
