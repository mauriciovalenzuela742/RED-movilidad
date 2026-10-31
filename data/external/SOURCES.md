# Fuentes externas — datos de transporte Santiago (RM)

Fecha de descarga: 2026-10-13

| Fuente | URL exacta usada | Fecha de descarga | Ruta local | Tamaño | Licencia | Observaciones |
|---|---|---|---|---|---|---|
| GTFS Red Movilidad (DTPM), vigente desde 03-oct-2026 | https://www.dtpm.cl/descargas/gtfs/GTFS_20261003.zip (enlace desde https://www.dtpm.cl/index.php/gtfs-vigente) | 2026-10-13 | `gtfs/GTFS_20261003.zip` (zip) y `gtfs/feed/` (descomprimido) | zip 12.820.680 bytes; descomprimido 69.711.027 bytes | No indicada en la página de descarga (no verificada) | Vigencia `feed_info.txt`: 20261003 a 20261231, versión V168.20261003. Otro feed (PO 06-dic-2025 + segunda vuelta) disponible en la misma página, no descargado. |
| OpenStreetMap, extracto Chile (Geofabrik) | https://download.geofabrik.de/south-america/chile-latest.osm.pbf | 2026-10-13 | `osm/chile-latest.osm.pbf` | 349.495.070 bytes | ODbL (datos OSM); licencia no comprobada en la página de Geofabrik | Archivo de Chile completo; no se encontró extracto más pequeño que cubra la RM. Última modificación en servidor: 2026-10-13 22:26:01 GMT. MD5 verificado contra `chile-latest.osm.pbf.md5` de Geofabrik. |
| Copernicus GLO-30 DEM, tile S34 W071 | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_S34_00_W071_00_DEM/Copernicus_DSM_COG_10_S34_00_W071_00_DEM.tif | 2026-10-13 | `dem/Copernicus_DSM_COG_10_S34_00_W071_00_DEM.tif` | 41.680.780 bytes | Términos Copernicus (no verificados en la página) | Cabecera TIFF válida (II*). Cubre Santiago urbano. |
| Copernicus GLO-30 DEM, tile S34 W072 (vecino) | https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_S34_00_W072_00_DEM/Copernicus_DSM_COG_10_S34_00_W072_00_DEM.tif | 2026-10-13 | `dem/Copernicus_DSM_COG_10_S34_00_W072_00_DEM.tif` | 33.279.328 bytes | Términos Copernicus (no verificados en la página) | Cabecera TIFF válida (II*). Existe en el bucket (HTTP 200). |
| Feriados públicos Chile 2025 (Nager.Date) | https://date.nager.at/api/v3/PublicHolidays/2025/CL | 2026-10-13 | `holidays/CL_2025.json` | 3.206 bytes | No verificada | JSON válido, 17 feriados. |
| Feriados públicos Chile 2026 (Nager.Date) | https://date.nager.at/api/v3/PublicHolidays/2026/CL | 2026-10-13 | `holidays/CL_2026.json` | 3.206 bytes | No verificada | JSON válido, 17 feriados. |
| Estaciones de carga EV, RM (OSM vía Overpass) | https://overpass-api.de/api/interpreter (GET, `amenity=charging_station`, bbox -34.3,-71.8,-32.9,-69.7, `out center tags`) | 2026-10-13 | `chargers/charging_stations_rm.json` | 43.371 bytes | ODbL (datos OSM) | 144 elementos (142 nodos, 2 vías). Timestamp de datos OSM reportado por el servidor: 2026-10-14T02:56:53Z (posterior a la fecha de descarga; tomar con cautela). Las vías vienen con `center`. Overpass devolvió 406 con POST; se usó GET con cabeceras Accept/User-Agent. |

## Notas

- Tamaño total de `data/external/`: aprox. 484 MB (límite 1,5 GB).
- En el GTFS, los `shape_id` tienen forma `201I` / `201R` / `201eI` (sin prefijo `T` y sin número de variante). Los `route_short_name` son `101`, `105c`, `201e`, etc. (sin prefijo `T`).
- Ninguna fuente fue ejecutada ni descomprimida fuera de `gtfs/feed/`.
