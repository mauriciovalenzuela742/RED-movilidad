"""Exporta las capas de referencia del repo para el mapa de velocidad (`mapa/`).

    python -m pulso.capas_mapa            # → mapa/capas/*.json (~1 min)

Capas (todas en JSON compacto, coordenadas con 5 decimales ≈ 1 m):
- `trazados.json`   trazados GTFS (`data/derived/shapes.parquet`), simplificados a ~5 m, con servicio y sentido.
- `pendiente.json`  tramos físicos de calle de ~100 m con su pendiente media (`segments` + `physical_segments`,
                    que salen de GTFS + DEM Copernicus GLO-30). Pendiente con signo en el sentido del tramo;
                    el mapa la colorea por su valor absoluto.
- `paraderos.json`  paraderos GTFS con los servicios que se detienen en cada uno (`shape_stops`).
- `cargadores.json` estaciones de carga de vehículos eléctricos de la RM (OSM, `data/external/chargers`).

La API del mapa las sirve en `/capas/<nombre>.json` si existe la carpeta (variable `CAPAS`, por defecto
`mapa/capas/`). Se regeneran cuando cambian las tablas de `data/derived/` (`python -m pulso.segments`).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd
import shapely

RAIZ = Path(__file__).resolve().parent.parent
DERIVADOS = RAIZ / "data" / "derived"
CARGADORES = RAIZ / "data" / "external" / "chargers" / "charging_stations_rm.json"
DESTINO = RAIZ / "mapa" / "capas"
TOLERANCIA_GRADOS = 0.00005  # ~5 m: simplificación de los trazados (Douglas-Peucker)


def _r(x: float) -> float:
    return round(float(x), 5)


def servicio_mapa(route_id: str) -> str:
    """route_id GTFS → servicio como lo nombra el lakehouse / el mapa ("405" → "T405", "B20" → "B20").

    Regla PROVISIONAL: la traducción lakehouse → GTFS sigue pendiente (PLAN.md, línea A) hasta ver los
    textos de ruta reales. Los servicios que parten con dígito llevan "T" adelante en el lakehouse.
    """
    return f"T{route_id}" if route_id[:1].isdigit() else route_id


def trazados() -> list[dict]:
    s = pd.read_parquet(DERIVADOS / "shapes.parquet")
    s = s[s.has_trips]
    salida = []
    for r in s.itertuples(index=False):
        linea = shapely.simplify(shapely.from_wkb(r.geometry), TOLERANCIA_GRADOS)
        salida.append({"id": r.shape_id, "servicio": servicio_mapa(r.route_id), "sentido": r.direction_letter,
                       "km": round(r.length_m / 1000, 1),
                       "camino": [[_r(x), _r(y)] for x, y in linea.coords]})
    return salida


def pendiente() -> list[dict]:
    seg = pd.read_parquet(DERIVADOS / "segments.parquet",
                          columns=["phys_id", "start_lon", "start_lat", "end_lon", "end_lat"])
    fis = pd.read_parquet(DERIVADOS / "physical_segments.parquet",
                          columns=["phys_id", "grade_pct", "n_routes", "length_m"])
    # geometría de cada tramo físico: la de su primer tramo de trazado (todos recorren la misma calle)
    geo = seg.drop_duplicates("phys_id").merge(fis, on="phys_id")
    return [{"a": [_r(r.start_lon), _r(r.start_lat)], "b": [_r(r.end_lon), _r(r.end_lat)],
             "p": round(float(r.grade_pct), 1), "n": int(r.n_routes)}
            for r in geo.itertuples(index=False)]


def paraderos() -> list[dict]:
    st = pd.read_parquet(DERIVADOS / "shape_stops.parquet",
                         columns=["shape_id", "stop_id", "stop_name", "stop_lon", "stop_lat"])
    rutas = pd.read_parquet(DERIVADOS / "shapes.parquet", columns=["shape_id", "route_id"])
    st = st.merge(rutas, on="shape_id")
    st["servicio"] = st.route_id.map(servicio_mapa)
    salida = []
    for (sid, nombre, lon, lat), g in st.groupby(["stop_id", "stop_name", "stop_lon", "stop_lat"]):
        nombre = re.sub(rf"^{re.escape(sid)}-", "", nombre)  # "PB1-Venezuela / esq. Bolivia" → sin el código
        salida.append({"id": sid, "nombre": nombre, "pos": [_r(lon), _r(lat)],
                       "servicios": sorted(g.servicio.unique().tolist())})
    return salida


def cargadores() -> list[dict]:
    elementos = json.loads(CARGADORES.read_text(encoding="utf-8"))["elements"]
    salida = []
    for e in elementos:
        punto = e if "lat" in e else e.get("center")
        if not punto:
            continue
        t = e.get("tags", {})
        enchufes = sorted({k.split(":")[1] for k in t if k.startswith("socket:") and k.count(":") == 1})
        salida.append({"pos": [_r(punto["lon"]), _r(punto["lat"])],
                       "nombre": t.get("name") or t.get("operator") or t.get("brand") or "Sin nombre",
                       "operador": t.get("operator") or t.get("brand") or "",
                       "capacidad": t.get("capacity") or "",
                       "enchufes": enchufes,
                       "uso": "buses" if t.get("bus") == "yes" or t.get("motor_vehicle") == "bus" else ""})
    return salida


CAPAS = {"trazados": trazados, "pendiente": pendiente, "paraderos": paraderos, "cargadores": cargadores}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--destino", type=Path, default=DESTINO)
    args = ap.parse_args(argv)
    args.destino.mkdir(parents=True, exist_ok=True)
    for nombre, fn in CAPAS.items():
        filas = fn()
        ruta = args.destino / f"{nombre}.json"
        ruta.write_text(json.dumps(filas, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{nombre}: {len(filas):,} elementos, {ruta.stat().st_size / 1e6:.1f} MB")
    print(f"Listo. Capas en {args.destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
