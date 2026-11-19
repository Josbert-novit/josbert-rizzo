"""Unidades de campo.

- "kilos", "kilogramos", "kg" -> kg/cm2 (en campo la presión se dice en kilos).
- Los manómetros locales: B-101 en kg/cm2, B-102 en psi, B-103 en bar (planta.yaml).
- Si el operador no dice la unidad, vale la del manómetro de ese equipo.
- Las comparaciones contra límites se hacen siempre en kg/cm2.
"""

from __future__ import annotations

from .numeros import normalizar

ALIAS = {"kg/cm2": "kg/cm2", "kg/cm²": "kg/cm2", "kilos": "kg/cm2", "kilo": "kg/cm2", "kg": "kg/cm2",
         "kilogramos": "kg/cm2", "psi": "psi", "libras": "psi", "lb": "psi", "bar": "bar", "bares": "bar",
         "m": "m", "metros": "m", "metro": "m", "degc": "degC", "°c": "degC", "grados": "degC", "c": "degC",
         "gotas/min": "gotas/min", "gotas por minuto": "gotas/min", "mm/s": "mm/s", "m3/d": "m3/d"}
DICHA = {"kg/cm2": "kilos", "psi": "psi", "bar": "bar", "m": "metros", "degC": "grados",
         "gotas/min": "gotas por minuto"}
A_KG = {"kg/cm2": 1.0, "psi": 1 / 14.2233, "bar": 1 / 0.980665}


def normalizar_unidad(u: str | None) -> str | None:
    if not u:
        return None
    return ALIAS.get(normalizar(u).strip(), u)


def a_kg(valor: float, unidad: str | None) -> float:
    """Convierte una presión a kg/cm2. Si la unidad no es de presión, devuelve el valor tal cual."""
    return valor * A_KG.get(normalizar_unidad(unidad) or "kg/cm2", 1.0)


def convertir(valor: float, de: str, a: str) -> float:
    de, a = normalizar_unidad(de), normalizar_unidad(a)
    if de == a:
        return valor
    if de in A_KG and a in A_KG:
        return valor * A_KG[de] / A_KG[a]
    raise ValueError(f"No convierto {de} a {a}")
