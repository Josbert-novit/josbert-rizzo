"""Respuestas del operador en la tablet, sin LLM (Lote 5).

`planta_kit.respuestas.interpretar` (librería de la cátedra) entiende las formas en que contesta el
operador: "Sí, confirmo.", "No. La uno estaba en ciento quince kilos, no en ciento doce.", "Eso de
uno coma ocho era de el filtro B, no de el filtro A.", "Lo de la dos es en psi, no en kilos.", "Te
faltó la dos, que estaba en …", "Lo de el pulmón no lo dije.", "Era la dos.", "¿Confirmar qué?".
Medido sobre las respuestas reales de `lote4b-completa`: entiende 94 de 94.

Acá se aplica lo entendido sobre nuestras filas (`FilaParteInterna`), con la misma lógica que
`planta_kit.respuestas.aplicar` (que trabaja sobre las filas de la librería).
"""

from planta_kit.respuestas import VARIABLE_POR_UNIDAD, Respuesta

from sistema.dominio.parte import FilaParteInterna, Parte
from sistema.reloj import reloj


def _nota(fila: FilaParteInterna, texto: str) -> None:
    fila.observacion = f"{fila.observacion} / {texto}" if fila.observacion else texto


def _resuelta(fila: FilaParteInterna) -> None:
    fila.requiere_confirmacion = False
    fila.motivo_confirmacion = None
    fila.ambiguedad_valor = False


def aplicar_correcciones(parte: Parte, respuesta: Respuesta, catalogo) -> None:
    """Modifica `parte.filas` según las correcciones entendidas."""
    filas = parte.filas
    for c in respuesta.correcciones:
        tag = c.get("tag")
        if c["tipo"] == "valor":
            cand = [f for f in filas if f.tag == tag and f.valor is not None]
            f = min(cand, key=lambda f: abs((f.valor or 0) - (c.get("mostrado") or 0)), default=None)
            if f and c.get("valor") is not None:
                f.valor = c["valor"]
                _resuelta(f)
        elif c["tipo"] == "equipo":
            f = next((f for f in filas if f.tag == c.get("tag_mostrado") and f.valor is not None
                      and abs(f.valor - (c.get("valor") or 0)) < 1e-6), None) \
                or next((f for f in filas if f.tag == c.get("tag_mostrado") and f.valor is not None), None)
            if f and tag:
                f.tag, f.ambiguo, f.candidatos = tag, False, []
        elif c["tipo"] == "unidad":
            for f in filas:
                if f.tag == tag and f.valor is not None and f.unidad == c.get("unidad_mostrada"):
                    f.unidad = c.get("unidad")
                    _resuelta(f)
        elif c["tipo"] == "falta":
            if not tag or c.get("valor") is None:
                continue
            unidad = c.get("unidad")
            existe = next((f for f in filas if f.tag == tag and f.valor is None and unidad == "gotas/min"
                           and f.variable == "goteo_empaquetadura"), None)
            if existe:  # el goteo dicho sin número: se le pone el número
                existe.valor, existe.unidad = c["valor"], unidad
                continue
            if unidad in ("kg/cm2", "psi", "bar") or unidad is None:
                variable = "presion_diferencial" if catalogo.tipo(tag) == "filtro" else "presion_descarga_local"
                unidad = unidad or ("kg/cm2" if variable == "presion_diferencial" else catalogo.unidad_manometro(tag))
            else:
                variable = VARIABLE_POR_UNIDAD.get(unidad, "lectura")
            hora = filas[0].hora if filas else reloj.ahora()
            nueva = FilaParteInterna(tag_dicho=tag, tag=tag, variable=variable, valor=c["valor"], unidad=unidad,
                                     hora=hora, fotos=list(filas[0].fotos) if filas else [])
            _nota(nueva, "agregada por corrección del operador")
            filas.append(nueva)
        elif c["tipo"] == "sobra":
            parte.filas = filas = [f for f in filas if not (f.tag == tag and f.valor is not None)]


def tiene_valores(parte: Parte) -> bool:
    return any(f.valor is not None for f in parte.filas)
