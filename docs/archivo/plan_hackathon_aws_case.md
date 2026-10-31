> **Archivado (31-oct-2026).** Plan original para el hackathon Breaking Barriers (AWS × U. de Chile, desafío CASE). El enfoque vigente está en [`PLAN.md`](../../PLAN.md). Se conserva porque el método técnico (capas C1–C6) sigue siendo la base.

# Plan — Breaking Barriers (AWS × U. de Chile), 14–16 oct 2026

Área: Sustentabilidad · Desafío 8, Electromovilidad eficiente (CASE) · Equipo: una persona + agentes.
Datos: posiciones GPS de RED Movilidad (Datopia Lakehouse). Cómputo pesado: NLHPC (Leftraru) vía SLURM.

## 0. Bases oficiales (leídas de la página del evento el 13-oct)

**Desafío 8 — Electromovilidad eficiente (CASE).** Contexto: la Estrategia Nacional de
Electromovilidad 2030 incluye flotas de carga en minería, construcción, agricultura y forestal;
autonomía, infraestructura de carga y planificación de rutas siguen siendo barreras. Tarea:
optimizar rutas de flotas eléctricas bajo restricciones operacionales y energéticas,
**usando datos reales de movilidad**.

**Evaluación:** innovación técnica 25% · impacto social 25% · factibilidad y sustentabilidad 20% ·
escalabilidad 15% · calidad de la presentación 15%.

**Tiempo real disponible: unas 16 horas de trabajo.**

| Bloque | Horas netas | Qué se hace |
|---|---|---|
| Día 1, 14:30–21:00 | ~5,5 | Fase 0 con datos reales, Fase 1, lanzar el procesamiento en NLHPC para la noche |
| Noche día 1 | — | El clúster procesa solo |
| Día 2, 09:00–13:00 | ~3,5 | Fases 2 y 3 |
| Día 2, 14:30–21:00 | ~6 | Fases 4 y 5 |
| Día 3, 09:00–10:15 | ~1 | Solo ensayo y video de respaldo |
| Día 3, 12:00 y 15:00 | — | Fase 6: pre-evaluación técnica y pitch de finalistas |

**Código previo: permitido** (confirmado por la organización; hubo talleres previos para preparar
la plataforma). La página no indica tamaño de equipo, servicios AWS obligatorios ni premios.

El desafío nombra minería, construcción, agricultura y forestal; nuestros datos son de Santiago
urbano. Posicionamiento: la pata urbana (logística de construcción y última milla en la RM) con
un método que se traslada a cualquier región que tenga GPS de flotas.

## 1. La idea en una frase

**Usar los ~7.000 buses de Santiago como sensores móviles de la red vial y convertir un año de
sus trazas GPS en un *mapa energético* de la ciudad, hora por hora, que le dice a una flota
eléctrica cuándo y por dónde circular para gastar menos kWh.**

Nombre de trabajo: **PulsoRED**.

Todos los equipos del desafío van a construir un optimizador de rutas. Casi ninguno tendrá
velocidades reales, medidas, calle por calle y hora por hora. Eso es lo que entregan los buses:
el solver pasa a ser la parte fácil y nuestra matriz de costo energético es lo que nadie más tiene.

## 2. Qué son los datos

- Ruta: `s3://do-datopia/categoria=transporte/pais=cl/fuente=red-movilidad/tipo=posicion-diaria/version=v1/year=/month=/day=`
- Desde 2025-09-09 hasta ayer, actualización diaria. Unas 7–13 M filas/día, 7.000–7.500 buses/día,
  140–260 MB/día. Extrapolado a hoy: unos 400 días, 4.000 M de filas, 80 GB (por confirmar).
- Columnas: `timestamp_gps_utc`, `timestamp_insertion_utc`, `plate`, `way` (I/R/vacío),
  `bus_route_consle`, `bus_route_assigned`, `operator_name`, `operator_number`, `speed` (km/h),
  `direction` (0–7), `geometry` (punto WKB, EPSG:4326).
- GeoParquet: requiere DuckDB + `spatial`. Credenciales STS que vencen cada hora.

Trampas conocidas:

- Las particiones son por día **UTC**; un día local ocupa dos particiones.
- Sin limpieza de origen: duplicados, saltos de GPS, velocidad 0 en terminales, `way` vacío.
- No hay columna que diga qué bus es eléctrico.
- Intervalo de muestreo desconocido (probablemente 30–60 s): demasiado grueso para aceleración
  segundo a segundo, por lo que el modelo de energía es mesoscópico.
- El GTFS descargado usa ids como `201I`, `201eR`, `B01I`; el lakehouse documenta `T201 03I`.
  Hay que escribir la regla de traducción al ver los strings reales.
- El GTFS en disco describe la red desde el 3-oct-2026. Para meses antiguos el calce con los
  trazados es menos fiable.

## 3. Diseño del análisis

Seis capas; cada una es un resultado en sí y la entrada de la siguiente.

**C1 — Trayectorias limpias.** Deduplicar por (patente, timestamp); eliminar puntos fuera de la
RM, teletransportes (> 120 km/h implícitos) y permanencia en terminales; cortar viajes por cambio
de ruta/sentido o huecos > 5 min. Reportar latencia del GPS como métrica de calidad.

**C2 — Calce al mapa sin map-matcher.** `bus_route_assigned` ya dice en qué ruta y sentido va el
bus. Proyectar cada punto sobre el trazado GTFS de esa ruta (referenciación lineal): da la
distancia recorrida sobre la ruta, mucho más barato y preciso que un HMM. Tramos de ~100 m.
Alternativa si el calce falla: hexágonos H3 (res. 10) × el campo `direction`.

**C3 — Atlas de velocidades.** Por tramo × sentido × franja de 15 min × tipo de día (laboral,
sábado, domingo, feriado): mediana, P15, P85, n. Se descuenta la detención en paraderos para que
la velocidad describa el tráfico y no la subida de pasajeros.

**C4 — Confiabilidad y stop-and-go.** Índice de buffer (P95/P50), detenciones por km, fracción
del tiempo bajo 5 km/h. Alimenta el modelo de energía y ordena los peores corredores.

**C5 — Modelo de energía.** kWh/km en función de velocidad media, detenciones por km, pendiente
(DEM Copernicus 30 m) y masa del vehículo, con frenado regenerativo. Parametrizado para camión
eléctrico y bus eléctrico.

**C6 — Capa de decisión.** Ruteo dependiente de la hora y de la energía (OR-Tools) con batería,
carga útil, ventanas de entrega y cargadores. Se compara contra un optimizador que supone flujo
libre, que es lo que hace una solución ingenua.

**Ajuste tras la revisión de literatura (`docs/parametros_energia.md`).** La evidencia publicada
no sostiene que el consumo de un vehículo eléctrico dependa de la velocidad media por sí sola:
CARB 2018 no ve tendencia y, en el modelo de Abdelaty 2021, lo que pesa son las detenciones por
km y la pendiente. La congestión cuesta energía por tres vías: detenciones, regeneración
incompleta y consumo auxiliar que corre con el tiempo. Consecuencias:

- C5 es un modelo físico por tramo (rodadura + pendiente + aerodinámica, energía cinética neta
  por detención, auxiliares × tiempo), calibrado contra consumos reales publicados.
- El atlas (C3/C4) debe entregar por tramo y franja **detenciones por km y tiempo de recorrido**,
  no solo velocidad mediana. Si el muestreo no permite contar detenciones, el resultado se debilita.
- Toda cifra de "X% más energía con congestión" es salida de nuestro modelo, con rango de
  sensibilidad. No existe una cifra publicada para Santiago y no se presenta como tal.
- Un modelo físico sin calibrar erró 43% en buses de Santiago (Basso 2024) contra 22% calibrado
  con telemetría: pedirle telemetría a CASE el día 1 es prioritario.

### Resultados a los que se apunta

Los números salen del pipeline; no se inventa ninguno.

1. "Procesamos N mil millones de puntos GPS de N buses en N días."
2. El primer atlas energético abierto de las calles de Santiago, hora por hora.
3. "La misma ruta de reparto gasta X% más batería a las 08:00 que a las 10:30."
4. "Un optimizador de flujo libre subestima la energía en X% y dejaría N de 100 camiones sin carga."
5. "Mover las salidas dentro de las ventanas de entrega ahorra X MWh y X tCO2 al año por cada 100 camiones."
6. Los 20 corredores donde la congestión desperdicia más energía.

### Límites que hay que decir antes de que los diga el jurado

- Un bus no es un camión: para en paraderos, usa pistas solo bus y evita calles de camiones.
  Mitigación: descuento de detenciones, tramos con pista bus marcados, mapa de cobertura a la vista.
- El modelo de energía se calibra con literatura salvo que CASE entregue telemetría. Preguntar el día 1.
- La validación es contra días no vistos de los mismos buses, no contra camiones reales.

## 4. Infraestructura: NLHPC para calcular, AWS para servir

### NLHPC (Leftraru) — verificado la noche del 13-oct

- Acceso por llave SSH funcionando (`ssh nlhpc`, puerto 4603).
- **Los nodos de cómputo tienen internet**: llegan a la API de Datopia, a S3 en us-west-2 y a PyPI
  (probado con jobs en `main`, `general` y `debug`).
- Particiones: `main` (27 nodos, 256 núcleos AMD, 727 GB), `general` (48 nodos, 44 núcleos Intel,
  180 GB), `largemem`, `debug` (30 min).
- Cada nodo tiene disco local en `/tmp` (166–916 GB libres).
- **El home está en 99 GB usados** (cuota de 100 GB según la sesión de agosto; hoy no pude leer
  la cuota) y no hay `/scratch`. Casi todo es de ALCeS (`DATASIM_LSST_1` 59 GB, `run_SNANA` 18 GB).
- Autoprueba en `main`: entorno armado en el nodo y 39 tests aprobados en 68 s. La misma
  prueba en `general` (Intel) también aprobó los 39 tests, así que sirven ambas particiones.

**Regla: todo cálculo sube como job SLURM.** Nada se ejecuta en el nodo de login salvo
`sbatch`, `squeue`, `sacct` y la copia de código.

Diseño para convivir con el home lleno:

- El entorno Python se construye en `/tmp` del nodo dentro del propio job (módulo
  `python/3.12.3-legacy-skylake`) y se reutiliza si ya existe en ese nodo.
- Cada job descarga su día a `/tmp` del nodo, procesa ahí y solo escribe al home el resultado
  agregado (MB, no GB). Los datos crudos nunca tocan el home.
- Patrón de trabajo: **job array, una tarea por día**, con tope de tareas simultáneas
  (`--array=1-N%12`), reanudable: la tarea termina de inmediato si su salida ya existe.

Con esto el **histórico completo vuelve a ser alcanzable** en una noche (400 tareas cortas en
paralelo), en vez de la muestra de 8 semanas. La muestra queda como plan B. Falta medir la
velocidad de descarga desde us-west-2, que es lo que fija el tiempo real.

Archivos en `slurm/`:

| Archivo | Uso |
|---|---|
| `sync.sh` | Desde el notebook: copia el código a `~/pulso` en Leftraru |
| `_env.sh` | Lo carga cada job: módulo, entorno en `/tmp`, carpeta temporal |
| `00_selftest.sbatch` | Arma el entorno, corre los tests y el ping |
| `01_coverage.sbatch` | Cobertura: qué días existen, filas y buses por día |
| `02_quality.sbatch` | Perfil de calidad de tres días |
| `10_days_array.sbatch` | Plantilla del array por día para las fases 1 y 2 (el módulo que llama aún no existe) |

Pendientes del lado NLHPC:

- Liberar al menos 5–10 GB del home antes del día 1 por la tarde, o las salidas no caben. Qué
  borrar de ALCeS es decisión tuya; no toqué nada.
- Las credenciales de Datopia deben existir en `~/pulso/config.json` del clúster (`chmod 600`).
- Las horas de CPU se cargan a tu cuenta NLHPC; confirma que este uso cabe en tu proyecto.

### AWS — el producto

- **S3** (bucket propio): tablas derivadas (atlas, costos energéticos, ranking de corredores).
- **Amazon Bedrock (Claude) con uso de herramientas**: copiloto del despachador.
- **Lambda + API Gateway**: endpoint del optimizador (OR-Tools en contenedor).
- **Front**: una página con deck.gl en S3 + CloudFront o Amplify.

Para el criterio de escalabilidad: decir con franqueza que el precálculo se hizo en NLHPC y que
el mismo job por día se traslada a AWS Batch.

## 5. Fases, agentes y modelos

Orquesta **Opus 5.5** (esta sesión): define el método, revisa cada salida, decide en las
compuertas y escribe el relato. Los agentes Sonnet/Haiku reciben tareas acotadas con prueba de
aceptación. Como el equipo es una sola persona, tu tiempo va a tres cosas: decidir en las
compuertas, hablar con CASE y los mentores, y ensayar el pitch.

### Fase 0 — Acceso y reconocimiento · hecha salvo los datos reales

| Tarea | Quién | Estado |
|---|---|---|
| Entorno 3.12, cliente Datopia con refresco de credenciales | Sonnet | Hecho, 39 tests |
| Script de cobertura | Haiku | Hecho, sin probar contra S3 |
| Perfil de calidad | Sonnet | Hecho, sin probar contra S3 |
| Datos externos: GTFS, OSM, DEM, feriados, cargadores | Haiku | Hecho, 484 MB |
| Bases del desafío | Opus | Hecho |
| Conexión NLHPC y jobs SLURM | Opus | Hecho; ver sección 9 |

**Compuerta G0 (día 1, primera media hora):** ¿mediana del intervalo de muestreo ≤ 60 s? ¿Más
del 80% de los puntos en movimiento calzan con un trazado GTFS? Si no, C2 pasa a hexágonos H3.

### Fase 1 — Trayectorias limpias a escala (día 1, tarde)

| Tarea | Quién | Listo cuando |
|---|---|---|
| Regla de traducción ruta lakehouse → `shape_id` GTFS | Opus | tasa de calce por operador |
| SQL de limpieza (C1) por día | Sonnet | tests sobre día sintético |
| Referenciación lineal, tramos de 100 m, marca de paraderos | Opus | revisión visual en 3 rutas |
| Módulo `--date` para el array SLURM, reanudable | Sonnet | 3 días procesados en `debug` |
| Lanzar el array completo para la noche | Opus | jobs en cola, monitoreo con `sacct` |

### Fase 2 — Atlas y confiabilidad (día 2, mañana)

| Tarea | Quién | Listo cuando |
|---|---|---|
| Job de reducción: atlas por tramo × franja × tipo de día (C3) | Sonnet | tabla del atlas |
| Métricas de confiabilidad y stop-and-go (C4) | Sonnet | ranking de corredores |
| Validación en días no vistos, MAPE por hora | Opus | tabla de error aceptada |
| Contraste con velocidades comerciales publicadas por DTPM | Haiku | nota con fuentes |

**Compuerta G2:** MAPE < 20% (objetivo) y mapa de cobertura honesto. Se congela el atlas.

### Fase 3 — Modelo de energía (día 2, mañana)

| Tarea | Quién | Listo cuando |
|---|---|---|
| Parámetros de consumo de camión y bus eléctrico, con citas | Sonnet | tabla con fuentes |
| Pendiente por tramo desde el DEM | Haiku | columna de pendiente |
| Función de energía y kWh por tramo y franja (C5) | Opus | análisis de sensibilidad |
| Resultados de ciudad: atlas energético, corredores, CO2 | Sonnet | tablas para el pitch |

### Fase 4 — Optimizador y copiloto (día 2, tarde)

| Tarea | Quién | Listo cuando |
|---|---|---|
| Matrices de energía/tiempo dependientes de la hora sobre OSM | Sonnet | < 2 s para 150 paradas |
| EVRP con ventanas, batería y carga | Opus diseña, Sonnet implementa | resuelve el caso demo |
| Comparación contra flujo libre en 3 escenarios | Sonnet | tabla: kWh, km, atrasos, camiones varados |
| Agente Bedrock con 3 herramientas | Sonnet | 5 preguntas guionadas correctas |

### Fase 5 — Producto y relato (día 2, tarde, en paralelo con la 4)

| Tarea | Quién | Listo cuando |
|---|---|---|
| Front: mapa animado, comparación de rutas, chat | Sonnet | funciona en el notebook de la demo |
| Figuras en un solo estilo | Sonnet | 6 figuras finales |
| Revisión hostil: un agente hace de jurado y ataca cada cifra | Opus | cada afirmación trazable a una consulta |

**Congelar a las 21:00 del día 2.**

### Fase 6 — Entregable para la segunda parte (día 3)

Entiendo "segunda parte" como el tramo de evaluación del día 3: cierre de desarrollo 10:15,
pre-evaluación técnica 12:00 y pitch de finalistas 15:00. Si las bases definen otra cosa, se ajusta.

**Paquete de entrega (listo a las 10:15):**

| Pieza | Contenido | Quién |
|---|---|---|
| Demo en vivo | URL pública + copia local que funciona sin internet | Sonnet |
| Video de respaldo | 2 min, la demo completa grabada, en dos equipos | Haiku asiste |
| Repositorio | Código, `README` con cómo reproducir, jobs SLURM, licencia | Sonnet |
| Ficha técnica (1 página) | Arquitectura, datos, método, validación, límites | Opus |
| Hoja de cifras | Cada número del pitch con su consulta y fecha | Opus |
| Presentación | 6 láminas, en español | Opus |
| Hoja de traspaso a CASE | Qué recibe la institución y qué necesita para desplegarlo | Opus |

**Pre-evaluación técnica (12:00).** Aquí miran si funciona y si es real. Guion de 4 minutos:
dato crudo → atlas → una ruta optimizada → la misma ruta con flujo libre → pregunta al copiloto.
Tener abiertos el repositorio, la hoja de cifras y el mapa de cobertura. Preguntas esperables con
respuesta preparada: bus ≠ camión, validación, por qué NLHPC y no AWS, costo de operarlo.

**Pitch de finalistas (15:00).** Tres minutos, una idea por lámina, ordenadas por los criterios:

1. El problema: el camión eléctrico que no llega (impacto).
2. El hallazgo: 7.000 buses ya miden la ciudad (innovación).
3. El atlas energético, animado 24 h.
4. El resultado: comparación contra flujo libre, con el número principal.
5. Cómo se usa mañana en CASE y cuánto cuesta (factibilidad).
6. Cómo escala: otras ciudades, otros GPS de flota, minería (escalabilidad).

Ensayar dos veces con cronómetro el día 3 entre 09:00 y 10:15. Nada de código nuevo ese día.

**Después del evento.** Los prototipos ganadores se entregan a la institución para un despliegue
inicial con créditos de AWS. La hoja de traspaso deja escrito: cuentas y permisos necesarios,
actualización diaria del atlas, y qué datos de CASE mejorarían el modelo.

## 6. Uso de la solución

**Quién la usa y para qué**

| Usuario | Qué hace | Qué obtiene |
|---|---|---|
| Despachador de una flota eléctrica | Carga las entregas del día siguiente y su flota | Rutas, horas de salida, batería restante por parada, dónde cargar |
| Gerente de flota | Simula comprar N camiones o instalar un cargador | Cuántas rutas actuales quedan cubiertas y con qué margen |
| CASE | Evalúa corredores y ubicación de cargadores | Mapa de energía por tramo y hora, demanda de carga estimada |
| DTPM / municipios | Consulta el atlas | Corredores donde la congestión desperdicia más energía |

**Flujo de uso del despachador (el que se muestra en la demo)**

1. Sube un CSV con las entregas (dirección, ventana horaria, kg) o las dicta al copiloto:
   "mañana tengo 12 camiones de 80 kWh y 140 entregas entre Quilicura y Maipú".
2. El sistema geocodifica, consulta el atlas para ese tipo de día y resuelve.
3. Ve el mapa con las rutas, la hora de salida recomendada por camión y la batería prevista al
   llegar a cada parada, con el margen de seguridad en color.
4. Compara con el plan "ingenuo": kWh, km, entregas atrasadas, camiones que no vuelven.
5. Pregunta variantes en lenguaje natural: "¿y si salgo a las 6?", "¿qué pasa si el camión 4
   lleva 2 toneladas más?". El copiloto vuelve a llamar al optimizador y explica el porqué del cambio.
6. Exporta la hoja de ruta por conductor (CSV; PDF si alcanza el tiempo).

**Qué necesita la institución para operarlo**

- Una cuenta AWS con Bedrock habilitado y el bucket con el atlas.
- Actualización del atlas: un job diario por día nuevo (el mismo job del array, en AWS Batch).
- Datos propios opcionales que lo mejoran: telemetría de consumo real de sus camiones para
  calibrar el modelo, y la ubicación de sus cargadores.

**Qué no hace** (decirlo en la demo): no ve el tráfico en tiempo real, planifica con el patrón
histórico del tipo de día; no cubre calles sin recorridos de bus; no reemplaza la decisión del
despachador.

## 7. Control de alcance (equipo de una persona)

Recortar en este orden: despliegue en Lambda (correr local) → cargadores en el solver → análisis
estacional → histórico completo (usar 8 semanas recientes) → exportación a PDF.
No recortar nunca: la validación, la comparación contra flujo libre y el video de respaldo.

## 8. Lo que falta para arrancar

1. Cuenta del Datopia Lakehouse. Sin ella nada corre contra datos reales.
2. Liberar espacio en el home de NLHPC.
3. Cuenta AWS con Bedrock (ver si la organización entrega una).

## 9. Guía del día 1

Construido y probado sin datos reales; nada ha corrido aún contra el lakehouse.

En el notebook (comprobación rápida):

1. Poner el login en `config.json` (raíz del proyecto), formato de `vendor/datopia-notebooks/config.example.json`.
2. `.venv\Scripts\python.exe -m pulso.ping`

En NLHPC (todo por SLURM):

3. `bash slurm/sync.sh` y copiar las credenciales:
   `scp config.json nlhpc:~/pulso/ && ssh nlhpc "chmod 600 ~/pulso/config.json"`
4. `ssh nlhpc "cd ~/pulso && sbatch slurm/00_selftest.sbatch"` — el ping debe mostrar filas de un día.
5. `ssh nlhpc "cd ~/pulso && sbatch slurm/01_coverage.sbatch --stage1-only"`
6. `ssh nlhpc "cd ~/pulso && sbatch slurm/02_quality.sbatch 2026-10-07 2026-10-10 2026-10-11"`
   (laboral, sábado y domingo).
7. Leer `~/pulso/outputs/phase0/quality/quality_report.md`: sección de strings de ruta y compuerta G0.
8. Decidir G0 y escribir la regla ruta → `shape_id`.

Seguimiento: `ssh nlhpc "squeue -u mvalenzuela"` y `ssh nlhpc "sacct -X -S today -o jobid,jobname,state,elapsed"`.

## 10. Estado tras la noche del 13-oct y riesgos abiertos

Hecho y probado sin datos reales (72 tests): cliente Datopia, cobertura, perfil de calidad, jobs
SLURM en NLHPC, capa de tramos de calle con pendiente (`docs/segmentos.md`), parámetros de
energía con fuentes (`docs/parametros_energia.md`) y núcleo del optimizador (`pulso/evrp.py`).

Riesgos que deciden el resultado, en orden:

1. **El optimizador aún no demuestra ahorro de energía.** En la demo sintética (150 paradas, 12
   camiones) el plan informado cumple todo, pero gasta 6% más kWh que el plan de flujo libre
   conducido en tráfico real; este último tiene 5 entregas atrasadas y 1 camión bajo la reserva.
   Hoy lo que se puede sostener es "planes que se cumplen", no "menos energía". Causas probables:
   tiempos de banda en el peor caso y convergencia débil. Tarea de Opus el día 2: comparar a
   igual nivel de cumplimiento y revisar la estrategia de bandas. Los números sintéticos no se citan.
2. **Detenciones por km.** El modelo de energía las necesita; depende del muestreo real del GPS.
3. **Regla ruta lakehouse → trazado GTFS**, y el 20% de trazados que se solapan consigo mismos.
4. **Agrupación de tramos por calle** revisada solo con conteos; falta verla en un mapa.
5. **Cargadores en el optimizador: no implementados** (era el primer recorte previsto).
6. **Home de NLHPC casi lleno** y login de Datopia pendiente.
