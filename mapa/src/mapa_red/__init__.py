"""
Mapa de velocidad de buses RED (Santiago).

Módulos:
    config      rutas (parquet diarios, caché) y período por defecto
    esquema     reglas sobre los datos que comparten la caché y las consultas (una sola fuente)
    cache       construcción de la caché desde los parquet diarios (comando `mapa-cache`)
    consultas   consultas en Polars sobre la caché y los parquet diarios
    api         API FastAPI y la página (comando `mapa-api`)
"""
