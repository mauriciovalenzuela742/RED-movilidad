# Plan — RED Movilidad con Data Observatory

Actualizado: 31-oct-2026. Reemplaza el plan del hackathon AWS × U. de Chile (desafío CASE),
archivado en [`docs/archivo/plan_hackathon_aws_case.md`](docs/archivo/plan_hackathon_aws_case.md).

## 1. Qué cambió

El proyecto deja de ser una entrega de hackathon centrada en un optimizador de rutas para flotas
eléctricas y pasa a ser un trabajo con **Data Observatory (DO)**, operador del Datopia Lakehouse,
sobre los datos GPS de los buses de RED Movilidad. El objetivo es:

1. **Mejorar los datos**: medir y documentar su calidad y producir versiones limpias y
   enriquecidas que otros puedan usar.
2. **Visualizarlos**: que se entienda qué dicen sobre la ciudad sin tener que escribir SQL.
3. **Desarrollar aplicaciones**: mostrar usos concretos (velocidades, confiabilidad, energía,
   electromovilidad) construidos sobre esos datos.

Lo que se mantiene: el método técnico por capas (C1–C6), el cliente del lakehouse, las capas de
referencia GTFS/DEM y la infraestructura NLHPC. Lo que sale: plazos del evento, pitch, criterios
de evaluación del jurado y el foco exclusivo en camiones eléctricos de CASE. El optimizador
(`pulso/evrp.py`) queda como una aplicación posible, no como el centro.

## 2. Los datos

- Lakehouse: `s3://do-datopia/categoria=transporte/pais=cl/fuente=red-movilidad/tipo=posicion-diaria/version=v1/`
- Desde 2025-09-09, actualización diaria; hueco sin datos entre 2026-07-06 y 2026-08-28 (según DO).
- ~7–13 M filas/día, ~7.000–7.500 buses/día, 140–260 MB/día. GeoParquet, partición por día UTC.
- Columnas y trampas conocidas: ver [`README.md`](README.md) y la sección 2 del plan archivado.

## 3. Líneas de trabajo

### A. Calidad y mejora de los datos (primero)

| Tarea | Estado |
|---|---|
| Cobertura: qué días existen, filas y buses por día (`pulso/coverage.py`) | Código y tests; falta correrlo contra S3 |
| Perfil de calidad: duplicados, saltos, latencia, muestreo, strings de ruta (`pulso/quality.py`) | Código y tests; falta correrlo contra S3 |
| Regla de traducción ruta lakehouse (`T201 03I`) → `shape_id` GTFS (`201I`) | Pendiente; requiere ver strings reales |
| Trayectorias limpias por día (C1) y calce lineal a trazados GTFS (C2) | Diseñado; pendiente |
| Informe de calidad para DO con hallazgos y propuestas de corrección en origen | Pendiente |
| Tabla derivada propuesta para el lakehouse (puntos limpios + tramo + distancia sobre ruta) | Pendiente; formato a acordar con DO |

### B. Visualización

| Tarea | Estado |
|---|---|
| Mapa de cobertura (dónde y cuándo hay datos) | Pendiente |
| Atlas de velocidades por tramo × franja de 15 min × tipo de día (C3) | Pendiente; depende de A |
| Confiabilidad y stop-and-go por corredor (C4) | Pendiente; depende de A |
| Notebook demo en el formato de `datopia-notebooks` (español, Colab) | Pendiente |

### C. Aplicaciones

| Aplicación | Estado |
|---|---|
| Atlas energético para electromovilidad (C5, `docs/parametros_energia.md`) | Parámetros con fuentes; modelo pendiente |
| Ruteo de flotas eléctricas dependiente de la hora (C6, `pulso/evrp.py`) | Núcleo hecho con costos sintéticos; sin resultados reales |
| Indicadores de servicio para DTPM / municipios (corredores lentos, regularidad) | Idea |

## 4. Infraestructura

- **Local**: entorno Python 3.12 en `.venv`, tests offline (`python -m pytest -q`).
- **NLHPC (Leftraru)**: cómputo pesado por SLURM, un job por día (`slurm/`). Regla: nada de
  cálculo en el nodo de login; los datos crudos van a `/tmp` del nodo y solo vuelven agregados.
- **Lakehouse DO**: se lee directo desde S3 con DuckDB y credenciales STS de 1 h.

## 5. Próximos pasos

1. Conseguir/confirmar la cuenta del Datopia Lakehouse y correr `python -m pulso.ping`.
2. Correr cobertura y perfil de calidad sobre 3 días (laboral, sábado, domingo).
3. Decidir la compuerta G0: mediana del intervalo de muestreo ≤ 60 s y > 80% de puntos en
   movimiento calzando con un trazado GTFS. Si no, el calce pasa a hexágonos H3.
4. Acordar con DO qué entregables quieren de vuelta (informe, tabla derivada, notebooks) y en
   qué formato.

## 6. Riesgos abiertos

1. Regla ruta lakehouse → `shape_id` y el ~20% de trazados que se solapan consigo mismos.
2. Si el muestreo GPS es grueso, no se pueden contar detenciones por km (afecta C4 y C5).
3. El GTFS en disco describe la red desde el 3-oct-2026; para meses antiguos el calce es menos fiable.
4. El modelo de energía no está calibrado con telemetría real.
5. Home de NLHPC casi lleno (99/100 GB).
