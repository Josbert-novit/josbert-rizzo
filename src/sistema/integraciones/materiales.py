"""Repuestos, stock por almacén y catálogo de tareas estándar. Dato propio del sistema de
mantenimiento (ver `src/sistema/datos/materiales.yaml`), no de la planta."""

from functools import lru_cache
from pathlib import Path

import yaml

_ARCHIVO = Path(__file__).resolve().parent.parent / "datos" / "materiales.yaml"


@lru_cache(maxsize=1)
def _cargar() -> dict:
    return yaml.safe_load(_ARCHIVO.read_text(encoding="utf-8"))


def repuestos() -> list[dict]:
    return _cargar()["repuestos"]


def repuesto(codigo: str) -> dict | None:
    return next((r for r in repuestos() if r["codigo"] == codigo), None)


def lista_materiales(equipo: str) -> list[str]:
    return _cargar()["lista_materiales"].get(equipo, [])


def tareas_estandar(equipo: str | None = None) -> list[dict]:
    tareas = _cargar()["tareas_estandar"]
    return [t for t in tareas if equipo is None or equipo in t["equipos"]]


def tareas_no_estandar() -> list[dict]:
    return _cargar()["tareas_no_estandar"]
