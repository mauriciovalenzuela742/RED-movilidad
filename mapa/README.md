# Mapa de velocidad de buses RED

Visualizador de la velocidad de los buses RED de Santiago a partir de sus posiciones GPS: una API en FastAPI
con las consultas en Polars, y una página estática con deck.gl y MapLibre.

- **Calor**: velocidad promedio por celda de ~100 m, por tipo de día y hora (con animación por hora). Con un
  servicio o recorrido filtrado, encima va su trayectoria típica, cada tramo del color de la velocidad de la
  celda por donde pasa (cambia con la hora).
- **Recorridos**: las celdas de cada recorrido en un color (los 5 con más registros; el resto, en gris como
  "Otros"), con su trayectoria típica como línea (requiere filtrar por servicio, sentido, recorrido o bus).
- **Trayecto**, con una barra de reproducción al pie del mapa: ▶ (o la barra espaciadora), línea de tiempo,
  velocidad (1 min a 2 h del día por segundo) y "día completo". Al reproducir, el día completo queda en
  gris, lo recorrido en color, los últimos 20 min más marcados y cada bus en marcha como una micro con su número de recorrido y su
  patente (se puede ocultar; las que se taparían se omiten hasta acercar el mapa). Se reproduce en el
  navegador, sin volver a pedir datos. Las líneas se colorean por **velocidad** (la misma escala del mapa de
  calor) o por **recorrido**; nunca por bus, porque con decenas de buses los colores se repetirían. Cada bus
  se identifica por su patente, el tooltip y el resaltado: al pasar el mouse por un bus (en el mapa o en la
  lista) su trayecto completo queda encima. Los puntos separados por un hueco de señal (más de 90 s) no se
  unen con una recta.
  - **Un bus** (al elegir su patente): todos sus puntos GPS de un día operativo (04:00 a 04:00), por defecto
    un color por recorrido. Si además hay un servicio, sentido o recorrido filtrado, solo esa parte de su día.
  - **Todos los buses** de un servicio o recorrido (filtrado, sin bus): los que lo hicieron ese día, por
    defecto coloreados por velocidad. Clic en un bus (en el mapa o en la lista) para ver solo ese.
- **Ranking de recorridos**, ordenable por cualquier columna (por defecto, más registros primero). Clic en
  una fila para verlo.

- **Capas de referencia** (agregadas en RED-movilidad): trazados GTFS, pendiente de las calles (GTFS + DEM
  Copernicus), paraderos y cargadores eléctricos, encima de cualquier vista. Las exporta
  `python -m pulso.capas_mapa` (raíz del repo) a `capas/` (variable `CAPAS`); el servidor las sirve en
  `/capas/<nombre>.json` y la página las pide al activarlas (`js/referencia.js`). Sin `capas/`, no aparecen.

Filtros: servicio, sentido, recorrido y bus (patente). La vista queda en la URL (filtros, modo, métrica,
hora y día), así que se puede compartir o guardar, ej. `/?recorrido=T301 00R&patente=BJFV-17&modo=trayecto`.

```
mapa-velocidad-red/
├── demo.ipynb             descarga los datos desde el Datopia Lakehouse y explora el dataset
├── config.example.json    plantilla de credenciales (copiar a config.json)
├── src/mapa_red/
│   ├── config.py          rutas y período (variables de entorno)
│   ├── esquema.py         reglas sobre los datos que comparten caché y consultas: servicio, sentido,
│   │                      día operativo (una sola fuente)
│   ├── cache.py           construcción de la caché desde los parquet diarios (comando mapa-cache)
│   ├── consultas.py       consultas en Polars sobre la caché (no leen los parquet diarios)
│   ├── api.py             API FastAPI y la página (comando mapa-api)
│   └── static/
│       ├── index.html     la página
│       ├── css/           estilos (Material Design 3 con los colores de RED)
│       ├── js/            módulos ES, sin compilación: main.js conecta los controles; estado.js guarda el
│       │                  estado compartido; el resto, una parte de la vista cada uno
│       └── vendor/        MapLibre 4.7.1 y deck.gl 9.1.14 autohospedados
└── tests/                 pytest con datos sintéticos (caché, consultas y API)
```

## Datos

No vienen en el repo. Hacen falta los parquet diarios de posiciones de buses RED en
`datos_local/year=YYYY/month=MM/day=DD/*.parquet`, con las columnas `timestamp_gps_utc`, `plate`,
`bus_route_assigned`, `operator_name`, `operator_number`, `speed`, `lon` y `lat`. Solo los lee `mapa-cache`:
la API sirve todo (también los trayectos) desde la carpeta de la caché, y es lo único que hay que desplegar.

**Descargarlos**: `demo.ipynb` se conecta al Datopia Lakehouse (Data Observatory) con credenciales
temporales de S3 y descarga un mes a `datos_local/` (celda "Descarga completa a Parquet local"), además de
explorar el dataset. Necesita una cuenta en el lakehouse: copia `config.example.json` a `config.json` y
completa tu email y contraseña (`config.json` no se sube al repo). Sus dependencias (duckdb, requests,
pandas, matplotlib, plotly) están en el grupo `notebook` de `pyproject.toml`; `uv sync` las instala.

## Cómo correrlo

```bash
uv sync

# 1. Caché del período (la primera vez tarda varios minutos con un mes completo).
#    Si la caché existe pero es de una versión anterior, se reconstruye sola.
uv run mapa-cache --mes 2025-09

# 2. API + página en http://127.0.0.1:8900
uv run mapa-api --periodo 2025-09
```

Las rutas se pueden cambiar con las variables de entorno `DATOS_LOCAL` (parquet diarios, solo para construir
la caché; por defecto `datos_local/`), `CACHE_BASE` (carpeta que contiene `cache_polars_<periodo>/`; por
defecto la raíz del repo)
y `PERIODO` (el que sirve la API si no se da `--periodo`). En un servidor basta con las dependencias de la
app: `uv sync --no-default-groups`.

## La caché

`mapa-cache` arma `cache_polars_<periodo>/` desde los parquet diarios:

| Archivo | Una fila es | Lo usan |
|---|---|---|
| `agg/grupo=NN.parquet` | celda de ~100 m × recorrido × bus × hora × tipo de día, con sumas para las métricas | mapa de calor, rankings, Recorridos |
| `celdas_global.parquet` | lo mismo sin recorrido ni bus | la vista sin filtros |
| `ranking_<dim>.parquet` | un servicio, sentido, recorrido o bus, con sus sumas | rankings y listas de los filtros |
| `dias_bus.parquet` | bus × día operativo × recorrido, con sus registros con posición | días con datos; recorridos de un bus en un día |
| `trayectos/dia=AAAA-MM-DD.parquet` | un punto GPS con posición, con su número de viaje | Trayecto (un bus o todos los de un recorrido) |
| `tipicas.parquet` | un recorrido, con su trayectoria típica | la línea de Calor y Recorridos |
| `meta.json` | — | versión, tamaño de celda y días por tipo |

Los trayectos van ordenados por recorrido, bus y hora, en pedazos (row groups) de 20 000 filas. Así, al
filtrar por servicio o recorrido se leen solo los pedazos que lo tienen, y para encontrar un bus se usan sus
recorridos de ese día, que están en `dias_bus`. Se escriben con pyarrow en vez de Polars porque permite elegir
la codificación de cada columna, y con eso pesan la mitad: unos 53 MB por día en septiembre de 2025.

Todo se lee en modo lazy (`scan_parquet`), y los datos se traen a memoria con `collect` solo cuando hace
falta, casi siempre al final de cada consulta. `sink_parquet` aparece solo al construir la caché, para escribir
cada archivo sin juntarlo entero en memoria.

## Desarrollo

```bash
uv run pytest            # tests con datos sintéticos (no necesitan los datos reales)
uv run ruff check src tests
```

## Formato de las respuestas

- `/vista`: lo que pide la página al cambiar los filtros, en **una** respuesta. Es el mismo binario de
  `/celdas` y, en su cabecera, las listas de los filtros con sus métricas (también el ranking), la vista por
  recorrido y, con `dias=1`, los días con datos. Antes eran hasta 7 pedidos por cambio de filtro: en Lambda
  cada uno podía ser un arranque en frío. Con `v` igual a la caché vigente (la página la toma de
  `config.js`), la respuesta se puede guardar en el navegador o en CloudFront, porque no cambia hasta que se
  reconstruye la caché. Los demás endpoints (`/celdas`, `/ranking`, `/por_recorrido`, `/dias`) siguen
  devolviendo cada parte por separado.
- `/celdas`: binario `[uint32 largo][cabecera JSON][arreglos]` con las matrices `[tipo de día, hora, celda]`,
  armado con Polars (sin numpy). La página elige y anima la hora sin volver a pedir datos. Los conteos van
  como UInt16 y pasan a UInt32 solo si alguno no cabe (una celda de terminal llega a ~95 000 registros por hora
  en un mes).
- `/ranking`, `/por_recorrido`, `/tipicas`, `/dias`, `/trayecto` y `/trayectos`: JSON escrito por Polars
  (`df.write_json()`, desde Rust), 5–8 veces más rápido que convertir a diccionarios y usar `JSONResponse`.
- La vista sin filtros se calcula y comprime una vez al arrancar.
- Un pedido que no se puede responder (filtro faltante, día mal escrito) devuelve 400 con
  `{"detail": "…"}`; la página muestra ese mensaje con un botón para reintentar.

**Viajes**: un viaje nuevo empieza cuando cambia el recorrido asignado, hay más de 15 min sin señal o el GPS
salta (el tramo implicaría más de 120 km/h; en un día típico hay ~4 500 saltos en ~1 600 buses). Sin ese
último corte, cada salto se dibujaba como una recta que cruza la ciudad.

**Trayectoria típica**: un viaje real y representativo de cada recorrido. Se elige un bus-día con registros
cercanos a la mediana del recorrido y, de sus viajes de ese recorrido, el que va más lejos de punta a punta
(de terminal a terminal). No el de más puntos: ese suele ser una ida y vuelta con el letrero sin cambiar. En
un circuito (parte y termina en el mismo lugar) se elige el de más puntos. Se elige una por recorrido al
construir la caché (`tipicas.parquet`).

**Datos verificados** (septiembre de 2025): particiones por fecha UTC, sin registros duplicados, un registro
cada 30 s por bus y `speed` en km/h instantánea (coincide con la velocidad calculada por posición). Como el
muestreo es parejo, el "% detenido" mide la fracción de tiempo detenido.
