"""Detector local de condiciones inseguras (S1 a S4). Sin red, sin modelos: regex sobre el texto transcripto.

Requisito (enunciado B, anexo Entrega 1): la alerta sale en el momento, sin enlace y sin nube, en menos de
30 s reales, y sin falsas alarmas (cada una resta). Por eso:

- frases específicas, no palabras sueltas ("gas" solo no alcanza: "olor a gas" sí);
- negaciones ("no hay olor a gas", "sin humo") no disparan;
- corre ANTES que cualquier LLM y no depende de que el parte se confirme.

    detectar("Se rompió algo en la descarga de la uno, sale agua a presión") -> Alerta(codigo="S1", ...)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .numeros import normalizar

PATRONES: dict[str, list[str]] = {
    # S1 · pérdida con proyección de agua a presión
    "S1": [r"agua a presion", r"sale agua (?:a|con) (?:mucha )?presion", r"chorro de agua", r"proyeccion de agua",
           r"sale agua para todos lados", r"se rompio (?:algo|un niple|el niple|la linea|el cano|la descarga)",
           r"revento (?:el|la|un|una) (?:niple|linea|cano|manguera|brida)", r"pinchadura con (?:chorro|proyeccion)"],
    # S2 · olor a gas
    "S2": [r"olor a gas", r"huele a gas", r"perdida de gas", r"fuga de gas", r"olor (?:fuerte )?a hidrocarburo"],
    # S3 · humo o fuego
    "S3": [r"\bhumo\b", r"olor a quemado", r"\bfuego\b", r"\bllamas?\b", r"incendio", r"se prendio fuego",
           r"esta quemando"],
    # S4 · persona lesionada
    "S4": [r"lesionad[oa]", r"herid[oa]", r"se (?:golpeo|lastimo|corto|quemo|cayo|desmayo|accidento)",
           r"esta sangrando", r"sangrando", r"se fracturo", r"accidente", r"necesita (?:un )?medico",
           r"llamen? a la ambulancia"],
}
_NEGACION = re.compile(r"\b(?:no|sin|ni|nada de|ningun|ninguna)\s+(?:\w+\s+){0,2}$")
_COMPILADOS = {c: [re.compile(p) for p in ps] for c, ps in PATRONES.items()}

MENSAJE = {
    "S1": "Pérdida con proyección de agua a presión",
    "S2": "Olor a gas",
    "S3": "Humo o fuego",
    "S4": "Persona lesionada",
}
TEXTO_TABLET = ("ALERTA DE SEGURIDAD: {motivo}. Alejate de la zona y aplicá el procedimiento de emergencia. "
                "Avisá YA por radio al supervisor de turno.")


@dataclass
class Alerta:
    codigo: str
    motivo: str
    frase: str

    def texto_tablet(self) -> str:
        return TEXTO_TABLET.format(motivo=self.motivo)


def detectar(texto: str) -> list[Alerta]:
    """Todas las condiciones inseguras del texto (puede haber más de una). Lista vacía si no hay."""
    t = normalizar(texto)
    alertas: list[Alerta] = []
    for codigo, rxs in _COMPILADOS.items():
        for rx in rxs:
            for m in rx.finditer(t):
                previo = t[max(0, m.start() - 25):m.start()]
                if _NEGACION.search(previo):
                    continue
                alertas.append(Alerta(codigo, MENSAJE[codigo], m.group(0)))
                break
            if alertas and alertas[-1].codigo == codigo:
                break
    return alertas
