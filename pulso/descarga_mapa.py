"""Descarga del lakehouse los parquet diarios que necesita el mapa de velocidad (`mapa/`).

    python -m pulso.descarga_mapa --desde 2026-10-06 --hasta 2026-10-13

Escribe `mapa/datos_local/year=YYYY/month=MM/day=DD/datos.parquet` (una partición **UTC** por día) con solo
las columnas que usa la app: `timestamp_gps_utc`, `plate`, `bus_route_assigned`, `operator_name`,
`operator_number`, `speed`, `lon` y `lat` (estas dos salen de `geometry`). Así pesa bastante menos que
copiar todo el dataset.

- El día operativo del mapa va de 04:00 a 04:00 hora local y abarca dos particiones UTC: para ver un día
  completo hay que bajar también el día UTC siguiente (`--hasta` un día después del último que se quiere ver).
- Usa las mismas credenciales que el resto de `pulso` (`config.json`, `sts.json` o variables de entorno).
  Renueva la sesión antes de cada día; con credenciales STS manuales, si vencen, se reemplaza `sts.json` y
  se vuelve a correr: los días ya bajados se saltan.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from . import datopia as d

DESTINO_POR_DEFECTO = d.PROJECT_ROOT / "mapa" / "datos_local"
# Columnas que lee mapa/src/mapa_red (cache.py y consultas.py), salvo lon/lat que se derivan aparte
COLUMNAS = ["timestamp_gps_utc", "plate", "bus_route_assigned", "operator_name", "operator_number", "speed"]


def archivo_dia(destino: Path, dia: date) -> Path:
    return Path(destino) / f"year={dia:%Y}" / f"month={dia:%m}" / f"day={dia:%d}" / "datos.parquet"


def _select_sql(con, origen: str) -> str:
    """SELECT con las columnas del mapa según el esquema que tenga ese día (lon/lat o geometry)."""
    tipos = {fila[0]: fila[1] for fila in con.execute(f"DESCRIBE SELECT * FROM {origen} LIMIT 0").fetchall()}
    partes = [c if c in tipos else f"NULL AS {c}" for c in COLUMNAS]
    if "lon" in tipos and "lat" in tipos:
        partes += ["lon", "lat"]
    elif "geometry" in tipos:
        geom = "geometry" if tipos["geometry"].upper().startswith("GEOMETRY") else "ST_GeomFromWKB(geometry)"
        partes += [f"ST_X({geom}) AS lon", f"ST_Y({geom}) AS lat"]
    else:
        raise d.DatopiaError(f"Sin coordenadas: no hay lon/lat ni geometry en {origen}")
    return f"SELECT {', '.join(partes)} FROM {origen}"


def descargar_dia(con, dia: date, destino: Path, base: str | None = None) -> int:
    """Copia un día UTC del lakehouse a `destino`. Devuelve las filas escritas."""
    salida = archivo_dia(destino, dia)
    salida.parent.mkdir(parents=True, exist_ok=True)
    tmp = salida.with_suffix(".parquet.tmp")
    origen = d.read_sql(d.day_path(dia, base))
    con.execute(f"COPY ({_select_sql(con, origen)}) TO {d._sql_str(tmp.as_posix())} "
                "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 500000)")
    tmp.replace(salida)  # atómico: un día a medio bajar nunca queda como datos.parquet
    return con.execute(f"SELECT count(*) FROM read_parquet({d._sql_str(salida.as_posix())})").fetchone()[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--desde", required=True, type=date.fromisoformat, help="Primer día UTC (YYYY-MM-DD)")
    ap.add_argument("--hasta", type=date.fromisoformat, help="Último día UTC, inclusive (por defecto = --desde)")
    ap.add_argument("--destino", type=Path, default=DESTINO_POR_DEFECTO, help="Carpeta datos_local del mapa")
    ap.add_argument("--rehacer", action="store_true", help="Volver a bajar días que ya existen")
    args = ap.parse_args(argv)
    hasta = args.hasta or args.desde
    if hasta < args.desde:
        ap.error("--hasta es anterior a --desde")
    dias = [args.desde + timedelta(days=i) for i in range((hasta - args.desde).days + 1)]

    try:
        con, session = d.connect()
        con.execute("SET preserve_insertion_order = false")  # baja mucho la memoria al escribir
        for dia in dias:
            if archivo_dia(args.destino, dia).exists() and not args.rehacer:
                print(f"{dia}: ya existe, se salta")
                continue
            session.ensure_fresh(con)
            t0 = time.time()
            try:
                filas = descargar_dia(con, dia, args.destino)
            except d.DatopiaError:
                raise
            except Exception as exc:  # p. ej. un día sin datos en el lakehouse
                print(f"{dia}: sin datos o error ({exc.__class__.__name__}: {str(exc).splitlines()[0]})",
                      file=sys.stderr)
                continue
            print(f"{dia}: {filas:,} filas en {time.time() - t0:.0f} s")
    except (d.CredentialsError, d.DatopiaError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Listo. Datos en {args.destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
