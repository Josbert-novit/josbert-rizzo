"""Catálogo de la planta: equipos, nombres informales, sensores, límites, repuestos y tareas.

Fuente única: `datos/planta.yaml` y `datos/materiales.yaml` del repo de la planta (o `GET /api/planta`).
Todo lo que sale de acá es determinístico: límites, stock y costos NO salen del RAG ni del modelo.

    cat = Catalogo.desde_archivos("../academia2026-tp/datos")
    cat.limite("B-101", "vibracion")      -> {"alarma_alta": 7.1, "disparo": 11.2}
    cat.unidad_manometro("B-102")         -> "psi"
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Variables locales (dichas en la recorrida) que se comparan contra el límite de un sensor conectado.
VARIABLE_SENSOR = {"presion_descarga_local": "presion_descarga", "presion_diferencial": "presion_diferencial",
                   "presion_diferencial_local": "presion_diferencial"}


@dataclass
class Catalogo:
    planta: dict
    materiales: dict
    equipos: dict[str, dict] = field(init=False)
    repuestos: dict[str, dict] = field(init=False)

    def __post_init__(self) -> None:
        self.equipos = {e["tag"]: e for e in self.planta["equipos"]}
        self.repuestos = {r["codigo"]: r for r in self.materiales["repuestos"]}
        self._limites = {(s["tag"], s["variable"]): s for s in self.planta["sensores"]}

    # ------------------------------------------------------------------ carga
    @classmethod
    def desde_archivos(cls, carpeta: str | Path | None = None) -> "Catalogo":
        carpeta = Path(carpeta or os.environ.get("PLANTA_DATOS", "datos"))
        leer = lambda n: yaml.safe_load((carpeta / n).read_text(encoding="utf-8"))  # noqa: E731
        return cls(leer("planta.yaml"), leer("materiales.yaml"))

    @classmethod
    def desde_api(cls, planta_api: dict, materiales: dict) -> "Catalogo":
        """`GET /api/planta` devuelve la misma definición que planta.yaml."""
        return cls(planta_api, materiales)

    # ------------------------------------------------------------------ equipos
    def tipo(self, tag: str) -> str | None:
        return self.equipos.get(tag, {}).get("tipo")

    def bombas(self) -> list[str]:
        return [t for t, e in self.equipos.items() if e["tipo"] == "bomba"]

    def filtros(self) -> list[str]:
        return [t for t, e in self.equipos.items() if e["tipo"] == "filtro"]

    def nombres(self) -> dict[str, list[str]]:
        return {t: e.get("nombres", []) for t, e in self.equipos.items()}

    def unidad_manometro(self, tag: str) -> str | None:
        return (self.equipos.get(tag, {}).get("manometro_local") or {}).get("unidad")

    def reserva_inicial(self) -> str | None:
        return next((t for t, e in self.equipos.items() if e.get("rol_inicial") == "reserva"), None)

    # ------------------------------------------------------------------ límites
    def limite(self, tag: str, variable: str) -> dict:
        """Límites de alarma de la hoja de datos (planta.yaml). Nunca los del manual Rev. C."""
        s = self._limites.get((tag, VARIABLE_SENSOR.get(variable, variable)))
        if not s:
            return {}
        return {k: s[k] for k in ("alarma_alta", "alarma_baja", "disparo") if k in s} | {"unidad": s["unidad"]}

    def sensores(self) -> list[dict]:
        return list(self.planta["sensores"])

    # ------------------------------------------------------------------ materiales y tareas
    def lista_materiales(self, tag: str) -> list[str]:
        return list(self.materiales["lista_materiales"].get(tag, []))

    def stock(self, codigo: str, almacen: str = "yacimiento") -> float:
        return float(self.repuestos.get(codigo, {}).get("stock", {}).get(almacen, 0))

    def costo(self, codigo: str) -> float | None:
        r = self.repuestos.get(codigo)
        return float(r["costo_usd"]) if r else None

    def tareas_estandar(self, tag: str | None = None) -> list[dict]:
        ts = self.materiales["tareas_estandar"]
        return [t for t in ts if tag is None or tag in t["equipos"]]

    def tareas_no_estandar(self) -> list[dict]:
        return list(self.materiales["tareas_no_estandar"])

    def tarea(self, codigo: str, tag: str | None = None) -> dict | None:
        """La tarea estándar para ese equipo (T-F-CAR cambia de repuesto según el filtro) o la no estándar."""
        for t in self.tareas_estandar(tag):
            if t["codigo"] == codigo:
                return t | {"estandar": True}
        for t in self.tareas_no_estandar():
            if t["codigo"] == codigo:
                return t | {"estandar": False, "repuestos": t.get("repuestos", [])}
        return None

    def es_estandar(self, codigo: str, tag: str) -> bool:
        return any(t["codigo"] == codigo for t in self.tareas_estandar(tag))

    def detiene(self, codigo: str, tag: str | None = None) -> bool:
        t = self.tarea(codigo, tag)
        return True if t is None else bool(t["detiene"])
