"""
Reglas sobre los datos que comparten la construcción de la caché y las consultas.

Viven en un solo lugar a propósito: si la caché y las consultas sobre los parquet diarios derivaran
servicio, sentido o día operativo de forma distinta, la API ofrecería días o recorridos que después no
encuentra (ej. `/dias` lista un día y `/trayecto` lo devuelve vacío).

Formato de `bus_route_assigned` = SERVICIO [VARIANTE] NN+SENTIDO, ej. "T504 00I", "T550 C0 00R":
    servicio = "T504" · sentido = I (ida) / R (regreso) · recorrido = el código completo.
"""

import polars as pl

ZONA = "America/Santiago"
# Día operativo (convención de transporte público): de 04:00 a 04:00 hora local. Así los viajes nocturnos
# que cruzan la medianoche quedan enteros en su día, y el día parte cuando salen los primeros buses.
HORA_INICIO_DIA_OPERATIVO = 4
PAUSA_NUEVO_VIAJE = 15 * 60  # s sin señal que cortan un viaje (y no cuentan para la velocidad comercial)
# km/h implícitos entre dos registros seguidos sobre los que el tramo es un salto del GPS, no un movimiento
# real: no cuenta para la velocidad comercial y corta el viaje (si no, se dibuja una recta que cruza la ciudad).
# En un día típico hay ~4 500 saltos en ~1 600 buses.
SALTO_GPS = 120

DIMENSIONES = ("servicio", "sentido", "recorrido", "patente")  # filtros del mapa
SUMAS = ["n_total", "n_mov", "suma_vel", "km_ruta", "s_ruta"]   # columnas de la caché que se suman al agregar
SIN_RECORRIDO = "(sin recorrido)"


def texto_o(col: str, defecto: str) -> pl.Expr:
    """Texto sin espacios al borde; vacío o nulo se reemplaza por `defecto`."""
    x = pl.col(col).str.strip_chars()
    return pl.when(x.is_null() | (x == "")).then(pl.lit(defecto)).otherwise(x)


# Recorrido de cada registro crudo (columna `bus_route_assigned`, limpia)
RECORRIDO = texto_o("bus_route_assigned", SIN_RECORRIDO)

# Servicio y sentido desde la columna `recorrido` ("T504 00I" → "T504" e "I"); sin sentido → "-"
_CON_SENTIDO = pl.col("recorrido").str.contains(r"\d{2}[IR]$")
SERVICIO = (pl.when(_CON_SENTIDO).then(pl.col("recorrido").str.split(" ").list.first())
            .otherwise(pl.col("recorrido")))
SENTIDO = pl.when(_CON_SENTIDO).then(pl.col("recorrido").str.slice(-1)).otherwise(pl.lit("-"))
# Códigos "TS" (fuera de servicio), ej. "T13TS 00I"
ES_TS = pl.col("recorrido").str.split(" ").list.first().str.contains(r"^T\d+TS$")

# Registros con posición: los únicos que se pueden dibujar. La lista de días por bus cuenta solo estos,
# para no ofrecer días cuyo trayecto saldría vacío.
CON_COORDENADAS = pl.col("lon").is_not_null() & pl.col("lat").is_not_null()


def km_entre(lon0: pl.Expr, lat0: pl.Expr, lon1: pl.Expr, lat1: pl.Expr) -> pl.Expr:
    """Distancia en km (aproximación equirectangular: exacta a < 0,1 % en las distancias de una ciudad)."""
    lat = lat0.cast(pl.Float64)
    return 111.32 * ((((lon1.cast(pl.Float64) - lon0.cast(pl.Float64)) * lat.radians().cos()) ** 2
                      + (lat1.cast(pl.Float64) - lat) ** 2).sqrt())


def km_desde_anterior(por: str | None = None) -> pl.Expr:
    """Distancia desde el registro anterior (filas ordenadas por hora); con `por`, dentro de cada grupo."""
    lon_ant, lat_ant = pl.col("lon").shift(1), pl.col("lat").shift(1)
    if por:
        lon_ant, lat_ant = lon_ant.over(por), lat_ant.over(por)
    return km_entre(lon_ant, lat_ant, pl.col("lon"), pl.col("lat"))


def hora_local(ts: str = "timestamp_gps_utc") -> pl.Expr:
    return pl.col(ts).dt.convert_time_zone(ZONA)


def dia_operativo(ts: str = "timestamp_gps_utc") -> pl.Expr:
    """Fecha del día operativo (04:00 a 04:00 local) de cada registro."""
    return (hora_local(ts) - pl.duration(hours=HORA_INICIO_DIA_OPERATIVO)).dt.date()
