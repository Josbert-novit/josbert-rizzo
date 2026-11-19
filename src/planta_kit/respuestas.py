"""Entender lo que contesta el operador en la tablet (planta/respuestas) y aplicarlo al borrador.

El operador habla como en campo. Formas que usa (ver planta_sim/dialogo.py):

    "Sí, confirmo." / "Correcto, dale." / "Perfecto, confirmo."                      -> confirma
    "No. La uno estaba en ciento quince kilos, no en ciento doce. Arreglá eso."      -> valor
    "No. Eso de uno coma ocho era de el filtro B, no de el filtro A."                 -> equipo
    "No. Lo de la dos es en psi, no en kilos."                                         -> unidad
    "No. Te faltó la dos, que estaba en ciento cincuenta gotas por minuto."           -> falta
    "No. Lo de el pulmón no lo dije."                                                  -> sobra
    "Era la dos." / "La correcta es el filtro A." / "Ninguna de esas, era la tres."   -> elige equipo
    "No te entendí, ¿me lo repetís?" / "¿Confirmar qué? Mostrame lo que anotaste."    -> reintentar

Si dice "Y hay más cosas, revisalo todo." corrige lo que nombró y vuelve a mostrar el borrador:
el operador nombra hasta dos problemas por vez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .nombres import Resolutor
from .notas import FilaNota
from .numeros import leer_numero, normalizar
from .unidades import normalizar_unidad

_NOMBRE = r"(?P<eq>(?:el|la|los)\s[\w\s-]+?)"
_VAL = r"(?P<v>[a-z0-9 ,\.]+?)"
RX = {
    "valor": re.compile(_NOMBRE + r" estaba en " + _VAL + r"(?:\s(?P<u>kilos|psi|bar|metros|grados|gotas por minuto))?,"
                        r" no en (?P<v2>[a-z0-9 ,\.]+?)\."),
    "equipo": re.compile(r"eso de " + _VAL + r" era de (?P<eq>[\w\s-]+?), no de (?P<eq2>[\w\s-]+?)\."),
    "unidad": re.compile(r"lo de (?P<eq>[\w\s-]+?) es en (?P<u>[\w\s/]+?), no en (?P<u2>[\w\s/]+?)\."),
    "falta": re.compile(r"te falto (?P<eq>[\w\s-]+?), que estaba en " + _VAL +
                        r"(?:\s(?P<u>kilos|psi|bar|metros|grados|gotas por minuto))?\."),
    "sobra": re.compile(r"lo de (?P<eq>[\w\s-]+?) no lo dije"),
}
CONFIRMA = re.compile(r"^(?:si\b|correcto|esta bien|perfecto|exacto|dale|confirmo|ok\b|de acuerdo)")
ELIGE = re.compile(r"^(?:era |la correcta es |es |ninguna de esas, era )?(?P<eq>(?:el|la)\s[\w\s-]+?)\.?$")
NO_ENTIENDE = re.compile(r"no te entendi|no te sigo|repetime|me lo repetis|confirmar que|de que nota")


@dataclass
class Respuesta:
    tipo: str                                  # confirma | corrige | elige | no_entiende | desconocida
    correcciones: list[dict] = field(default_factory=list)
    tag_elegido: str | None = None
    hay_mas: bool = False


def interpretar(texto: str, resolutor: Resolutor) -> Respuesta:
    t = normalizar(texto).strip()
    t_sin_no = re.sub(r"^no\.\s*", "", t)
    if NO_ENTIENDE.search(t):
        return Respuesta("no_entiende")
    if not t.startswith("no") and CONFIRMA.search(t):
        return Respuesta("confirma")
    correcciones: list[dict] = []
    for tipo, rx in RX.items():
        for m in rx.finditer(t_sin_no):
            g = m.groupdict()
            c = {"tipo": tipo, "tag": resolutor.resolver(g["eq"].strip())}
            if g.get("v"):
                n = leer_numero(g["v"])
                c["valor"] = n.valor if n else None
            if g.get("v2"):
                n = leer_numero(g["v2"])
                c["mostrado"] = n.valor if n else None
            if g.get("eq2"):
                c["tag_mostrado"] = resolutor.resolver(g["eq2"].strip())
            if g.get("u"):
                c["unidad"] = normalizar_unidad(g["u"].strip())
            if g.get("u2"):
                c["unidad_mostrada"] = normalizar_unidad(g["u2"].strip())
            correcciones.append(c)
    if correcciones:
        return Respuesta("corrige", correcciones, hay_mas="hay mas cosas" in t)
    m = ELIGE.match(t_sin_no)
    if m and (tag := resolutor.resolver(m.group("eq"))):
        return Respuesta("elige", tag_elegido=tag)
    return Respuesta("desconocida")


VARIABLE_POR_UNIDAD = {"gotas/min": "goteo_empaquetadura", "m": "nivel", "degC": "temperatura_rodamiento_ir"}


def aplicar(filas: list[FilaNota], r: Respuesta, catalogo) -> list[FilaNota]:
    """Devuelve el borrador corregido (no modifica la lista original)."""
    filas = [FilaNota(**vars(f)) for f in filas]
    for c in r.correcciones:
        tag = c.get("tag")
        if c["tipo"] == "valor":
            cand = [f for f in filas if f.tag == tag and f.valor is not None]
            f = min(cand, key=lambda f: abs((f.valor or 0) - (c.get("mostrado") or 0)), default=None)
            if f:
                f.valor = c["valor"]
        elif c["tipo"] == "equipo":
            f = next((f for f in filas if f.tag == c.get("tag_mostrado") and f.valor is not None
                      and abs(f.valor - (c.get("valor") or 0)) < 1e-6), None) \
                or next((f for f in filas if f.tag == c.get("tag_mostrado") and f.valor is not None), None)
            if f:
                f.tag = tag
        elif c["tipo"] == "unidad":
            for f in filas:
                if f.tag == tag and f.valor is not None and f.unidad == c.get("unidad_mostrada"):
                    f.unidad = c["unidad"]
        elif c["tipo"] == "falta":
            unidad = c.get("unidad")
            existe = next((f for f in filas if f.tag == tag and f.valor is None and unidad == "gotas/min"
                           and f.variable == "goteo_empaquetadura"), None)
            if existe:                                       # el goteo dicho sin número: le ponemos el número
                existe.valor, existe.unidad = c["valor"], unidad
                continue
            if unidad in ("kg/cm2", "psi", "bar") or unidad is None:
                variable = "presion_diferencial" if catalogo.tipo(tag) == "filtro" else "presion_descarga_local"
                unidad = unidad or ("kg/cm2" if variable == "presion_diferencial" else catalogo.unidad_manometro(tag))
            else:
                variable = VARIABLE_POR_UNIDAD.get(unidad, "lectura")
            filas.append(FilaNota(tag, variable, c["valor"], unidad))
        elif c["tipo"] == "sobra":
            filas = [f for f in filas if not (f.tag == tag and f.valor is not None)]
    return filas
