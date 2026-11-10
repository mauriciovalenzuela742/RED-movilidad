"""
Rutas y período por defecto. Se leen de variables de entorno y no al importar: así la API y los tests
pueden pasar sus propias rutas sin tocar el entorno.

    DATOS_LOCAL   parquet diarios `year=YYYY/month=MM/day=DD/*.parquet`  (por defecto: <repo>/datos_local)
    CACHE_BASE    carpeta que contiene `cache_polars_<periodo>/`         (por defecto: <repo>)
    PERIODO       período que sirve la API: mes YYYY-MM o día YYYY-MM-DD (por defecto: 2025-09)
    CAPAS         capas de referencia `<nombre>.json` (trazados, pendiente, paraderos, cargadores)
                  (por defecto: <repo>/capas; si no existe, el mapa funciona sin ellas)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]  # raíz del repo (src/mapa_red/config.py → ../../)
PERIODO_POR_DEFECTO = "2025-09"


@dataclass(frozen=True)
class Rutas:
    datos_local: Path  # parquet diarios
    cache_base: Path   # carpeta que contiene cache_polars_<periodo>/
    capas: Path | None = None  # capas de referencia (<nombre>.json, de `python -m pulso.capas_mapa`); opcional

    @classmethod
    def desde_entorno(cls) -> Rutas:
        return cls(datos_local=Path(os.environ.get("DATOS_LOCAL", RAIZ / "datos_local")),
                   cache_base=Path(os.environ.get("CACHE_BASE", RAIZ)),
                   capas=Path(os.environ.get("CAPAS", RAIZ / "capas")))

    def carpeta_cache(self, periodo: str) -> Path:
        return self.cache_base / f"cache_polars_{periodo}"


def periodo_por_defecto() -> str:
    return os.environ.get("PERIODO", PERIODO_POR_DEFECTO)
