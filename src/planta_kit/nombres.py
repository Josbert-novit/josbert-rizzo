"""Nombres informales -> TAG.

"la dos", "la del medio", "el de la izquierda", "el pulmón"... se resuelven contra el listado de la planta
(planta.yaml o GET /api/planta). "la bomba" o "el filtro" a secas son ambiguos: hay que preguntar.
"la de reserva" depende del estado: se resuelve con la reserva actual, no con la inicial.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .catalogo import Catalogo
from .numeros import normalizar

GENERICOS = {
    "bomba": re.compile(r"\b(?:la|esa|una) bomba\b(?!\s+(?:uno|dos|tres|1|2|3|b-?\s?10[123])\b)"),
    "filtro": re.compile(r"\bel filtro\b(?!\s+(?:a|b|primario|secundario|f-?\s?201[ab])\b)"),
}
EXTRA = {  # variantes que aparecen en transcripciones y en el habla
    "B-101": ["la 1", "bomba 1", "b-101", "b 101", "b101", "la numero uno"],
    "B-102": ["la 2", "bomba 2", "b-102", "b 102", "b102", "la numero dos"],
    "B-103": ["la 3", "bomba 3", "b-103", "b 103", "b103", "la numero tres"],
    "F-201A": ["f-201a", "f 201 a", "f201a", "filtro a"],
    "F-201B": ["f-201b", "f 201 b", "f201b", "filtro b"],
    "TK-301": ["tk-301", "tanque pulmon"],
    "FT-401": ["ft-401"],
}


@dataclass
class Mencion:
    inicio: int
    fin: int
    tag: str | None          # None = genérico ambiguo
    dicho: str
    tipo_generico: str | None = None


class Resolutor:
    def __init__(self, catalogo: Catalogo):
        self.catalogo = catalogo
        alias: dict[str, str] = {}
        for tag, nombres in catalogo.nombres().items():
            for n in nombres + EXTRA.get(tag, []):
                alias[normalizar(n)] = tag
        self.alias = alias
        orden = sorted(alias, key=len, reverse=True)
        self._re = re.compile(r"\b(" + "|".join(re.escape(a) for a in orden) + r")\b")

    def menciones(self, texto_normalizado: str, reserva_actual: str | None = None) -> list[Mencion]:
        """Todas las menciones de equipos, en orden. Usa texto ya normalizado (ver numeros.normalizar)."""
        t = texto_normalizado
        salida: list[Mencion] = []
        ocupado: list[tuple[int, int]] = []
        for m in self._re.finditer(t):
            tag = self.alias[m.group(1)]
            if m.group(1) in ("la de reserva",) and reserva_actual:
                tag = reserva_actual
            salida.append(Mencion(m.start(), m.end(), tag, m.group(1)))
            ocupado.append((m.start(), m.end()))
        for tipo, rx in GENERICOS.items():
            for m in rx.finditer(t):
                if not any(a <= m.start() < b for a, b in ocupado):
                    salida.append(Mencion(m.start(), m.end(), None, m.group(0), tipo))
        return sorted(salida, key=lambda x: x.inicio)

    def resolver(self, dicho: str, reserva_actual: str | None = None) -> str | None:
        """Un nombre suelto ("la del medio") -> "B-102". None si no lo conoce o es genérico."""
        n = normalizar(dicho).strip()
        if n == "la de reserva" and reserva_actual:
            return reserva_actual
        if n in self.alias:
            return self.alias[n]
        ms = self.menciones(n, reserva_actual)
        tags = {m.tag for m in ms if m.tag}
        return tags.pop() if len(tags) == 1 else None

    def nombre_corto(self, tag: str) -> str:
        """Cómo se lo nombra al operador: 'la uno', 'el filtro A'."""
        nombres = self.catalogo.nombres().get(tag) or [tag]
        return nombres[0]
