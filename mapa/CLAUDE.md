# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Visualizador de la velocidad de los buses RED de Santiago a partir de sus posiciones GPS: API FastAPI con
consultas en Polars y una página estática con deck.gl + MapLibre. Código, comentarios, mensajes y textos
de la interfaz van en español; los nombres de funciones y variables también.

## Comandos

```bash
uv sync                                   # todo: app + dev (pytest, ruff, httpx) + notebook
uv run mapa-cache --mes 2025-09           # construye cache_polars_2025-09/ (~4 min con un mes); --dia, --rehacer
uv run mapa-api --periodo 2025-09         # http://127.0.0.1:8900 (--puerto para otro)
uv run pytest                             # ~2 s, datos sintéticos: no necesita datos_local/
uv run pytest tests/test_consultas.py::test_trayecto_completo_y_filtrado
uv run ruff check src tests               # --fix ordena los imports
```

- Variables de entorno: `DATOS_LOCAL` (parquet diarios, por defecto `datos_local/`), `CACHE_BASE` (carpeta
  que contiene `cache_polars_<periodo>/`, por defecto la raíz) y `PERIODO`.
- Si `mapa-api` falla con `[Errno 10048]`, ya hay otro servidor en el puerto 8900.
- Los datos no están en el repo: `demo.ipynb` los descarga del Datopia Lakehouse a
  `datos_local/year=YYYY/month=MM/day=DD/*.parquet` (particiones por fecha **UTC**). Necesita `config.json`
  (copia de `config.example.json`, ignorado por git).
- No hay tests de JS ni build. Para verificar la página: `node --check` sobre una copia `.mjs` de cada
  módulo, y capturas con Chrome headless (`--headless=new --use-angle=swiftshader --enable-unsafe-swiftshader
  --virtual-time-budget=25000 --screenshot=...`; con `--enable-logging=stderr` se ven los errores de consola).
  El estado de la vista va en la URL (ej. `/?recorrido=T301 00R&patente=BJFV-17&modo=trayecto&dia=2025-09-17`),
  así que se puede capturar cualquier vista directo. Los errores de las consultas no llegan a la consola: la
  página los atrapa y los muestra en el aviso `#error`. Al verificar, revisar ese aviso, que `#cargando`
  terminó y que `#resumen` tiene cifras, no solo la consola.
- Para medir la reproducción (cuadros, tareas largas, clics) usar la GPU real (`--use-angle=d3d11
  --enable-gpu`): con SwiftShader el dibujo es por software y la reproducción de varios buses casi no avanza.
  Al hacer clic con CDP, primero `scrollIntoView`: lo que está bajo el pliegue del panel recibe el clic el mapa.

## Arquitectura

### Dos fuentes de datos, unas mismas reglas

- **Caché** (`cache.py`, sale de `mapa-cache`): agregados por celda de ~100 m × recorrido × patente × hora ×
  tipo de día. La usan el mapa de calor, los rankings, la vista Recorridos y la lista de días por bus. Se
  construye en 16 grupos por hash de patente (memoria acotada) y queda en `agg/grupo=NN.parquet`, ordenada
  por recorrido para que filtrar por servicio o recorrido salte row groups. Al filtrar por bus,
  `Cache.agg` lee solo el archivo de su grupo (~7× más rápido); el grupo de cada patente se guarda en
  `ranking_patente.parquet` al construir, porque `Expr.hash` no es estable entre versiones de Polars.
- **Parquet diarios crudos**: los usan los trayectos (`/trayecto`, `/trayectos`) y la trayectoria típica,
  que necesitan los puntos GPS en orden.
- `esquema.py` es la única definición de las reglas que ambas comparten: servicio y sentido desde
  `bus_route_assigned` (`"T550 C0 00R"` → `T550`, `R`), día operativo de **04:00 a 04:00 hora local**
  (abarca dos particiones UTC: `Cache.archivos_dia`), qué registros cuentan (`CON_COORDENADAS`) y qué es un
  salto de GPS (`SALTO_GPS`, 120 km/h: no cuenta para la velocidad comercial y corta los viajes). Si estas
  reglas difieren entre la caché y las consultas, `/dias` ofrece días que `/trayecto` devuelve vacíos. No hay
  que duplicarlas.
- Si cambia lo que escribe la caché, hay que subir `cache.VERSION`: `mapa-cache` reconstruye solo cuando la
  versión de `meta.json` no coincide, y la API se niega a arrancar con una caché de otra versión (con el
  comando para reconstruirla en el mensaje).

### API (`api.py`, `consultas.py`)

- `crear_app(periodo, rutas)` es una fábrica sin estado global. Al arrancar (lifespan) carga la `Cache` y
  precalcula lo que no depende de filtros (vista global, rankings completos, `/config.js`) en `app.state`.
  Los tests la instancian con rutas temporales.
- Respuestas: `/celdas` es binario `[uint32 largo][cabecera JSON][arreglos alineados a 4 bytes]`, con
  matrices `[tipo de día (2), hora (24), celda]` que salen del búfer Arrow de Polars (sin numpy). El
  navegador las lee como TypedArrays y elige o anima la hora sin volver a pedir datos. El resto es JSON
  escrito por Polars (`df.write_json()`), no por diccionarios ni `JSONResponse`, que es 5–8× más lento.
  Cada arreglo de `/celdas` declara su tipo (`consultas.TIPOS_BINARIOS`), y `js/calor.js` debe conocerlos
  todos; un test lo comprueba. La vista global (~14 MB) se comprime con gzip una vez al arrancar: comprimirla
  por pedido con el nivel 9 por defecto de `GZipMiddleware` tardaba 16 s.
- Los errores de entrada del usuario se lanzan como `ConsultaInvalida`, que un solo manejador convierte en
  400 `{"detail": ...}`. Un `ValueError` común sigue siendo 500.
- Las trayectorias típicas se guardan en `Cache.tipicas` la primera vez que se piden.

### Página (`static/`)

- Módulos ES sin compilación. `maplibregl`, `deck` y `window.CONFIG` (de `/config.js`) son globales, porque
  vienen de scripts clásicos que cargan antes que `js/main.js`. Las versiones de `vendor/` están fijas:
  MapLibre 4.7.1 y deck.gl 9.1.14.
- `js/estado.js` guarda el estado compartido en **objetos mutables** (`estado`, `datos`, `vista`, `tray`,
  `tipicas`, `rankOrden`), porque un `export let` no se puede reasignar desde otro módulo. Los módulos se
  importan en ciclo (panel ↔ tipicas ↔ capas…), lo que funciona porque solo se llaman entre sí dentro de
  funciones, nunca al evaluarse.
- Dos caminos de actualización:
  - `filtros.aplicarFiltros()` consulta al servidor (celdas, listas, por_recorrido, días) y después redibuja.
  - `panel.refrescar()` redibuja con lo ya cargado: métrica, hora, tipo de día. Corre en cada cuadro de
    animación, así que no debe pedir nada. Termina en `guardarUrl()`, que escribe la URL con retardo
    porque los navegadores limitan `history.replaceState`.
- Modos: `calor` | `recorrido` (requiere algún filtro) | `trayecto` (requiere bus, servicio o recorrido). Con
  bus, el trayecto muestra su día (o solo la parte filtrada); sin bus, todos los buses de lo filtrado ese día.
- `js/combobox.js` reemplaza la lista nativa de cada `<select>` por un menú con buscador, pero el `<select>`
  escondido sigue siendo la fuente del valor: el código asigna `.value`, reescribe `innerHTML` y escucha
  `change` como con un select normal.
- **Reproducción del trayecto** (`js/trayecto.js`): todo lo que se dibuja (caminos, tramos, puntos) se arma
  una vez al cargar y las capas reciben siempre los mismos arreglos y funciones de módulo. Por cuadro solo
  cambian `currentTime` de las `TripsLayer` y `filterRange` de la `DataFilterExtension` (una sola instancia),
  que son uniforms de la GPU. Recrear arreglos o accesores por cuadro hace que deck.gl recalcule los atributos
  y bloquee el hilo principal (así se perdían los clics de "Pausar"). La animación usa `requestAnimationFrame`.
- Botones cuyo ícono cambia: cambiar el texto del `.ms`, nunca el `innerHTML` del botón. Si el ícono se
  reemplaza entre el mousedown y el mouseup, el clic se pierde.
- Colores (`js/colores.js`): la paleta categórica tiene 5 colores, validados con la skill de dataviz con
  `--pairs all` contra el mapa base (#f5f5f4). En un mapa, con más de 5 siempre hay dos que se confunden, y
  por eso `consultas.MAX_RECORRIDOS_COLOR` también es 5. El resto va a "Otros" en gris y los colores nunca se
  reciclan. El trayecto se colorea por velocidad o por recorrido, nunca por bus.
- La interfaz es solo de escritorio (el ranking se oculta bajo 760 px). Estilos de Material Design 3 con la
  paleta de RED: tokens en `css/estilos.css` y colores de los datos en `js/colores.js`.

### Tests

`tests/conftest.py` genera parquet sintéticos con el esquema real (dos días, seis buses: un recorrido sin
posición, un punto fuera de Santiago, un salto de GPS y una ida y vuelta con el letrero sin cambiar) y
construye una caché real una vez por sesión. Las horas se escriben en local y se pasan a UTC con +3 (Chile
en septiembre).
