# Capa de referencia de tramos (`pulso/segments.py`)

Genera, a partir del GTFS del DTPM (vigente desde 2026-10-03) y el DEM Copernicus GLO-30, todo lo que
no depende del GPS: tramos de 100 m, paraderos, pendiente y una función de proyección.

    python -m pulso.segments --out data/derived      # ~2,3 min, 43 MB en total

Todas las distancias se calculan en UTM 19S (EPSG:32719). Las geometrías guardadas son lon/lat (EPSG:4326, WKB).

## Tablas

| Archivo | Filas | Contenido |
|---|---|---|
| `shapes.parquet` | 981 | Un `shape_id`: `route_id`, `direction_id`, `length_m`, `n_points`, `n_trips`, partes del id (`route_code`, `variant`, `pre_qualifier`, `direction_letter`, `tail_qualifier`), `n_segments`, `self_overlap_m`, `self_overlap`, `has_trips`, `geometry` (polilínea completa; la usa `project_points`). |
| `segments.parquet` | 197.030 | `shape_id`, `seg_idx`, `start_m`, `end_m`, `length_m`, lon/lat de inicio, fin y punto medio, `bearing_deg`, `h3_10` (celda H3 res 10 del punto medio), elevación crudo y suavizada en inicio y fin, `grade_raw_pct`, `grade_unclipped_pct`, `grade_pct` (el que se usa), `phys_id`, `geometry`. |
| `physical_segments.parquet` | 44.294 | Un `phys_id`: punto medio, rumbo, `h3_10`, `n_segments`, `n_shapes`, `n_routes` (códigos de ruta distintos), pendiente mediana/mín/máx. Es la unidad para juntar velocidades por calle. |
| `shape_stops.parquet` | 47.313 | Paraderos en orden por shape (859 shapes con viajes): `stop_pos`, `stop_id`, `stop_name`, `dist_m`, `offset_m`, `seg_idx`, `far_from_shape` (> 50 m). |

El GTFS trae 981 shape_id (no 982: ese número incluía la cabecera). 122 de ellos no tienen ningún viaje en `trips.txt`
(`has_trips = False`, `route_id` nulo, sin paraderos); son sobre todo variantes `PRN`, `PT`, `TNOC`.

## Patrones de `shape_id`

Se parsea con una regex jerárquica, no una sola forma. Todos los 981 ids calzan (962 forma principal, 19 con guion).

- Principal: `código de ruta` + `variante en minúscula` (opcional) + `calificador previo` (opcional) + `sentido I|R` + `calificador final` (opcional).
  - Códigos: `101` (3 dígitos) y `B01`, `E03`, `F30` (letra + 2 dígitos).
  - Variante minúscula: `c` (100), `e` (61), `v` (10); ej. `201eR`, `105cI`.
  - Calificador previo, solo `N` (32): `109NI`, `B02NI`.
  - Calificador final: `PRN` 117, `PM` 61, `PT` 25, `N` 14, `TNOC` 8, `PMPT` 4, `PM2` 3, `PRN2` 3, `PMTPM` 2, `PTTPT` 2, `TNOC2`, `TNOCPT`, `PT2` (1 cada una). Son variantes horarias/operacionales (punta mañana/tarde, nocturno), ej. `101IPM`, `426IPRN`, `428eITNOC`.
- Con guion (19): `L1-I`, `L4A-R`, `MTN-I`, `BAA-I`: código alfabético + `-` + sentido.
- Sentido: `I` 552, `R` 429. Calza 100 % con `direction_id` (I = 0, R = 1).
- Cada shape_id pertenece a una sola ruta y un solo sentido en `trips.txt`.

## Segmentación

- Cortes cada 100 m a lo largo del shape; el último es más corto. Excepción: un resto menor a 10 m se funde con el
  tramo anterior (que queda de hasta 110 m), para no crear tramos de pocos metros con rumbo y pendiente basura. Largo mediano 100 m, mínimo 10 m, máximo 110 m.
- Largo total sumando shapes: 19.664 km; mediana por shape 18,9 km (mín 2,3 km, máx 82 km).
- `bearing_deg`: azimut geodésico real (WGS84) de inicio a fin del tramo.
- Los vértices intermedios del shape se conservan en la geometría de cada tramo.
- Índice de tramo de un punto: `min(floor(dist_m / 100), n_segments - 1)` (`seg_index`), consistente con la tabla.

## Pendiente

1. Se muestrea el DEM (bilinear, mosaico de las dos teselas) cada 20 m a lo largo del shape.
2. Filtro de mediana móvil de 11 muestras (220 m), luego media móvil de 11 muestras (220 m). La mediana elimina puentes,
   pasos bajo nivel y picos de arbolado; la media suaviza el ruido del DEM de 30 m.
3. `grade_pct = (z_fin - z_inicio) / largo * 100` con el perfil suavizado en los límites del tramo, recortado a ±15 %.
4. Fuera del DEM no se calcula (NaN).

Distribución sobre 196.641 tramos con DEM (|pendiente| en %, mediana ≈ 0):

| | p1 | p5 | p95 | p99 | max abs | \|g\| > 6 % | \|g\| > 10 % | \|g\| > 15 % |
|---|---|---|---|---|---|---|---|---|
| Cruda (100 m sin suavizar) | -6,5 | -3,1 | 3,1 | 6,7 | 54,7 | 2,5 % | 0,51 % | 0,17 % |
| Suavizada sin recorte | -4,6 | -1,8 | 1,9 | 4,8 | 43,6 | 1,3 % | 0,28 % | 0,15 % |
| Final (`grade_pct`, recorte ±15) | -4,6 | -1,8 | 1,9 | 4,8 | 15,0 | 1,3 % | 0,28 % | 0 |

289 tramos (0,15 %) quedaron recortados. 389 tramos (0,2 %) caen fuera del DEM, todos en 2 shapes que pasan al sur de
la latitud -34,0 (las teselas cubren -34 a -33): tienen `grade_pct` nulo.

Verificación de signo: en la zona Providencia-Apoquindo-Las Condes (lon -70,60 a -70,54) la pendiente media de los
tramos con rumbo este (60-120°) es +1,63 %, y la de los rumbo oeste (240-300°) es -1,65 %; hacia Lo Barnechea
(lat -33,38 a -33,30) los tramos rumbo este promedian +1,30 %.

## Tramos físicos (`phys_id`)

Problema: varias rutas pasan por la misma calle, pero sus cortes de 100 m no coinciden (desfasados hasta 50 m), así
que una celda H3 fija partiría la misma calle de forma distinta según la ruta, y una unión transitiva por cercanía
fundiría calles enteras en un solo id.

Regla elegida (greedy con tramos canónicos): los shapes se procesan de más a menos viajes (desempate por largo). Los
tramos del primer shape son canónicos. Un tramo de otro shape se une al canónico más cercano si:

- los puntos medios están a ≤ 60 m,
- la distancia lateral (perpendicular al rumbo del canónico) es ≤ 20 m (misma calle, incluso calzadas paralelas pegadas),
- los rumbos difieren < 30° (sentidos opuestos nunca se juntan).

Si no calza con ninguno, funda un tramo físico nuevo. Como los canónicos son contiguos cada 100 m, el desfase máximo
entre un tramo y su canónico es ~50 m. El H3 res 10 se entrega como columna auxiliar, no como llave.

Resultado: 197.030 tramos se colapsan en 44.294 `phys_id` (4,4 a 1). 76 % de los ids físicos son compartidos por más de un shape (mediana 3, máx 60 shapes). Longitud de red única estimada ≈ 4.423 km.

Chequeo en corredores (tramos con punto medio a ≤ 35 m de una línea central aproximada, separados por sentido):

| Corredor | Sentido | Tramos | Shapes | phys_id | Ideal |
|---|---|---|---|---|---|
| Alameda (Est. Central - Plaza Italia, 4,5 km) | ida | 505 | 115 | 36 | ~45 |
| | vuelta | 471 | 107 | 38 | ~45 |
| Providencia (Plaza Italia - Tobalaba) | ida / vuelta | 137 / 80 | 46 / 49 | 16 / 11 | ~35 |
| Av. Vicuña Mackenna (Plaza Italia - Bellavista) | ida / vuelta | 155 / 179 | 73 / 63 | 44 / 41 | ~95 |
| Apoquindo | ida / vuelta | 23 / 16 | 23 / 16 | 5 / 3 | ~44 |

En la Alameda 115 shapes (73 códigos de ruta) producen solo 36-38 ids por sentido, con hasta 46 shapes en un mismo id:
la fusión funciona y no hay fragmentación (los ids son ≤ lo ideal). En los otros corredores los ids quedan bajo lo ideal
porque la línea central dibujada a mano es burda (curvas, tramo cubierto solo parcialmente por la ventana de 35 m) y
porque no todo el eje tiene buses; la señal útil es que no sobran ids (no hay más ids que km/0,1) y que hay 3 a 6 shapes por id mediana.
No se hizo inspección visual en mapa: solo conteos.

## Proyección (`project_points`, `project_dataframe`)

- `project_points(shape_id, lon, lat, prev_m=None)` devuelve `(dist_m, offset_m)`; el desfase es con signo
  (positivo a la izquierda del sentido de avance).
- Sin pista: punto más cercano en toda la polilínea (STRtree, vectorizado). Con `prev_m` o `sequential=True`: el arreglo
  se trata como secuencia ordenada; cada punto se busca entre `prev - 30 m` y `prev + 3000 m` (si el desfase ahí supera
  150 m se amplía hacia adelante) y la distancia nunca baja.
- `project_dataframe` agrupa por `shape_id` (+ `group_cols`, p. ej. viaje o patente) y usa el modo secuencial solo en
  shapes autosolapados, tomando el orden de filas como orden temporal. Agrega `dist_m`, `offset_m`, `seg_idx`.
- Autosolape: un shape se marca (`self_overlap`) si ≥ 100 m de su trazado están a < 20 m de otro tramo del mismo shape
  separado > 500 m a lo largo y en rumbo paralelo/antiparalelo (los cruces simples no cuentan). **194 de 981 shapes
  (20 %)** están marcados (mediana de 21 km de largo).
- Prueba con los propios puntos medios de los tramos (150.000 puntos): modo cercano acierta (< 5 m) 99,997 % en shapes
  normales y solo 84,9 % en autosolapados; el modo secuencial acierta 100 % en una muestra de 20 shapes autosolapados
  (el modo cercano, 86 %). Velocidad: cercano ~30.000 puntos/s; secuencial ~6.000 puntos/s (bucle Python).

## Paraderos

Representante por shape: el viaje con el patrón de paradas más frecuente (desempate: más paradas). Se proyectan en
modo secuencial con pista inicial 0 m; las distancias son monotónicas (0 retrocesos). 99 de 47.313 paraderos (60 shapes) quedan a > 50 m del
shape (mediana de esos 121 m; máximo 4,4 km: probable dato de paradero o shape erróneo). Quedan marcados
(`far_from_shape`) y no se eliminan.

## Debilidades conocidas

- El DEM Copernicus es un modelo de superficie (copas, edificios) a 30 m; la suavización de 220 m + 220 m reduce el
  ruido pero un túnel o viaducto largo (> ~110 m) sigue contaminando la pendiente. El recorte a ±15 % solo acota el daño.
- La pendiente es de la superficie del DEM, no de la calzada; en cortes y terraplenes puede diferir 1-2 puntos.
- `phys_id` depende del orden de proceso (greedy) y hasta un 50 m de desfase longitudinal se pierde al juntar. Calles
  paralelas a < 20 m del mismo rumbo (calzadas expresas) se fusionan.
- El modo secuencial asume que las filas vienen ordenadas en el tiempo y que el viaje parte cerca del inicio, o se le
  pasa `prev_m`. Tras un salto mayor a 3 km con desfase pequeño puede fijarse mal.
- La segmentación usa distancias UTM (factor de escala ~0,9999 en Santiago, despreciable).
- El GTFS describe la red desde 2026-10-03; para fechas anteriores los trazados pueden no coincidir.
- Los 122 shapes sin viajes se segmentan igual, pero sus paraderos no están.
