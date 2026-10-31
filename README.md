# RED-movilidad

Desarrollo con [Data Observatory](https://dataobservatory.net) (DO) en un proyecto de datos del
transporte público de Santiago. Trabajamos sobre las posiciones GPS de los buses de RED Movilidad
publicadas en el **Datopia Lakehouse** para mejorar esos datos, visualizarlos y desarrollar
aplicaciones sobre ellos (velocidades por calle, confiabilidad, consumo energético,
electromovilidad).

El plan vigente está en [`PLAN.md`](PLAN.md). El código Python vive en el paquete `pulso`.

> Origen: el proyecto nació como **PulsoRED** para el hackathon Breaking Barriers
> (AWS × U. de Chile, desafío CASE, oct-2026). Ese plan está archivado en
> [`docs/archivo/`](docs/archivo/plan_hackathon_aws_case.md).

---

## Estructura de carpetas

| Carpeta / archivo | Qué contiene |
|---|---|
| `pulso/` | Paquete Python del proyecto. `datopia.py`: login al lakehouse, credenciales STS de 1 h y conexión DuckDB. `ping.py`: prueba rápida de acceso. `coverage.py`: qué días existen y cuántas filas/buses tiene cada uno. `quality.py`: perfil de calidad (duplicados, saltos, muestreo, strings de ruta). `segments.py`: capa de referencia de tramos de 100 m desde GTFS + DEM. `evrp.py`: ruteo de flotas eléctricas dependiente de la hora (costos sintéticos por ahora). `testing.py`: datos sintéticos con el esquema real para tests. |
| `tests/` | Tests offline con `pytest` (no requieren credenciales ni internet). |
| `slurm/` | Jobs para el clúster NLHPC (Leftraru): `sync.sh` copia el código, `_env.sh` arma el entorno en el nodo, `00_selftest`, `01_coverage`, `02_quality` y la plantilla `10_days_array` (un job por día). |
| `data/external/` | Datos públicos descargados (ver tabla abajo y [`SOURCES.md`](data/external/SOURCES.md) con URL, fecha, tamaño y licencia de cada uno). |
| `data/derived/` | Tablas generadas por `python -m pulso.segments`: `shapes`, `segments` (197 mil tramos de 100 m con pendiente), `physical_segments` (44 mil tramos físicos de calle) y `shape_stops` (paraderos por trazado). Detalle en [`docs/segmentos.md`](docs/segmentos.md). |
| `docs/` | `segmentos.md`: cómo se construye la capa de tramos. `parametros_energia.md` / `.json`: parámetros de consumo de buses y camiones eléctricos con sus fuentes. `archivo/`: planes anteriores. |
| `outputs/` | Salidas de corridas. `evrp_demo.json` es una demo del optimizador con **costos sintéticos** (no son resultados). |
| `vendor/datopia-notebooks/` | Submódulo con los notebooks públicos de DO ([Data-Observatory/datopia-notebooks](https://github.com/Data-Observatory/datopia-notebooks)): documentación del lakehouse y demo de acceso. |
| `PLAN.md` | Enfoque y líneas de trabajo actuales. |
| `requirements.txt` / `requirements-hpc.txt` | Dependencias locales completas / mínimas para los jobs en NLHPC. |

---

## Bases de datos

### Principal: GPS de RED Movilidad (Datopia Lakehouse, Data Observatory)

Posiciones GPS de todos los buses del sistema RED, tomadas de la API `/posiciones` del DTPM y
consolidadas a diario por DO. **No es de descarga abierta**: requiere cuenta en el lakehouse
(se solicita a Data Observatory). No se guarda en este repositorio; se consulta directo desde S3.

- Ruta: `s3://do-datopia/categoria=transporte/pais=cl/fuente=red-movilidad/tipo=posicion-diaria/version=v1/year=/month=/day=`
- Cobertura: desde 2025-09-09, diaria (sin datos entre 2026-07-06 y 2026-08-28).
- Escala: ~7–13 M filas/día, ~7.000–7.500 buses/día, 140–260 MB/día.
- Formato: GeoParquet (punto WKB EPSG:4326), particionado por **día UTC**; requiere DuckDB + `spatial`.
- Columnas: `timestamp_gps_utc`, `timestamp_insertion_utc`, `plate`, `way` (I/R), `bus_route_consle`,
  `bus_route_assigned`, `operator_name`, `operator_number`, `speed` (km/h), `direction` (0–7), `geometry`.

### Complementarias: datos públicos descargados (`data/external/`)

Todas descargadas el 13-oct-2026. Detalle completo en [`data/external/SOURCES.md`](data/external/SOURCES.md).

| Base | Fuente | Para qué la usamos | Ruta | Tamaño | Licencia |
|---|---|---|---|---|---|
| GTFS Red Movilidad, vigente desde 03-oct-2026 (V168, válido hasta 31-dic-2026) | [DTPM](https://www.dtpm.cl/index.php/gtfs-vigente) | Trazados de recorridos, paraderos y horarios: base para calzar el GPS a la ruta | `gtfs/GTFS_20261003.zip` y `gtfs/feed/` | 12,8 MB zip / 70 MB | No indicada |
| Modelo digital de elevación Copernicus GLO-30 (tiles S34 W071 y S34 W072) | [Copernicus DEM en AWS](https://copernicus-dem-30m.s3.amazonaws.com/) | Pendiente de cada tramo de 100 m (consumo energético) | `dem/` | 75 MB | Términos Copernicus |
| OpenStreetMap, extracto Chile | [Geofabrik](https://download.geofabrik.de/south-america/chile-latest.osm.pbf) | Red vial completa para ruteo | `osm/` | 349 MB | ODbL |
| Feriados de Chile 2025 y 2026 | [Nager.Date](https://date.nager.at/) | Clasificar días (laboral, sábado, domingo, feriado) | `holidays/` | 6 KB | No verificada |
| Estaciones de carga de vehículos eléctricos, RM | OpenStreetMap vía [Overpass API](https://overpass-api.de/) | Ubicación de cargadores (144 elementos) | `chargers/` | 43 KB | ODbL |

**El archivo OSM no está en el repositorio** (supera el límite de 100 MB de GitHub). Para recuperarlo:

    curl -L -o data/external/osm/chile-latest.osm.pbf https://download.geofabrik.de/south-america/chile-latest.osm.pbf

---

## Puesta en marcha

    git clone --recurse-submodules https://github.com/mauriciovalenzuela742/RED-movilidad.git
    cd RED-movilidad
    pip install uv
    uv venv --python 3.12 .venv
    uv pip install --python .venv/Scripts/python.exe -r requirements.txt   # en Linux/macOS: .venv/bin/python

Activar el entorno: `source .venv/Scripts/activate` (Git Bash) o `.\.venv\Scripts\Activate.ps1` (PowerShell).

### Credenciales del lakehouse

Se buscan en este orden (los valores de ejemplo se ignoran):

1. variables de entorno `DATOPIA_EMAIL` y `DATOPIA_PASSWORD`
2. `config.json` en la raíz del proyecto
3. `vendor/datopia-notebooks/config.json`

Formato: `{"test_user": {"email": "tu@correo.cl", "password": "..."}}`. `config.json` está en `.gitignore`: **nunca se sube**.

### Uso

    python -m pulso.ping [YYYY-MM-DD]            # prueba de acceso: filas, buses y rango horario de un día UTC
    python -m pulso.segments --out data/derived  # regenera la capa de tramos (~2 min)
    python -m pytest -q                          # tests offline

```python
from pulso import datopia as d
con, session = d.connect()
session.ensure_fresh(con)             # llamar antes de cada consulta en procesos largos
con.sql(f"SELECT count(*) FROM {d.read_sql(d.day_path('2026-10-15'))}")
```

Las particiones son por día UTC. Para un día calendario de Santiago usar
`d.local_day_paths(day)` junto con `d.local_day_where(day)` (se necesitan ambos).
