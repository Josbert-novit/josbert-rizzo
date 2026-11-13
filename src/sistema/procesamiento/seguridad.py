"""Alerta de seguridad (S1-S4): agua a presión, olor a gas, humo/fuego, persona lesionada.

Estas condiciones **no vienen marcadas en el contrato** (`NotaRecorrida` no tiene un campo
"seguridad"; la planta no las anuncia a propósito, hay que detectarlas). La única señal es el
texto de la nota de voz, así que el detector corre sobre la transcripción apenas sale de Whisper,
antes de la extracción con el LLM (Ollama) y de cualquier otra llamada de red: tiene que funcionar
**local, sin nube y sin depender del enlace** (que en el escenario `fuga-sin-enlace` se corta), y
rápido, para publicar la alerta y el aviso en la tablet dentro de los 30 s reales que pide la
consigna. Por eso es un matcheo de palabras clave determinístico (regex), no un LLM: ni más lento
ni probabilístico — "cero falsas alarmas" es un criterio eliminatorio.

Las frases canónicas (`planta_sim/operador.py::nota_seguridad`) son fijas por código:
- S1: "...sale agua a presión para todos lados..."
- S2: "Hay olor a gas fuerte..."
- S3: "Sale humo del motor de..., hay olor a quemado."
- S4: "...se golpeó la mano con la llave..., está sangrando bastante."
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

from planta_contrato import EventoSistema, MensajeTablet, NotaRecorrida, Pantalla, TipoEvento


@dataclass
class Deteccion:
    codigo: str
    descripcion: str
    procedimiento: str


_DESCRIPCIONES = {
    "S1": "Pérdida con proyección de agua a presión",
    "S2": "Olor a gas",
    "S3": "Humo u olor a quemado en un motor",
    "S4": "Persona lesionada",
}

_PROCEDIMIENTOS = {
    "S1": "Alejate del chorro y cortá la bomba afectada si podés hacerlo sin exponerte. "
    "No toques el equipo presurizado. Aislá la zona.",
    "S2": "No enciendas nada ni generes chispas. Alejate de la zona y ventilá si es seguro hacerlo.",
    "S3": "Cortá la alimentación del motor si es seguro. No uses agua sobre equipo eléctrico. Alejate.",
    "S4": "Asistí a la persona sin moverla si sospechás una lesión grave. Pedí ayuda médica ya.",
}

# Cada condición es una lista de combinaciones; una combinación matchea si TODOS sus fragmentos
# aparecen en el texto (no necesariamente adyacentes). Varias combinaciones redundantes por
# código: la transcripción de Whisper puede comerse o deformar una palabra puntual (ver el bug
# real documentado en BUGS.md: "a presión" transcripto como "aprecian"), así que no alcanza con
# una sola frase exacta — hace falta más de un camino para llegar al mismo código.
_PATRONES: dict[str, list[list[str]]] = {
    "S1": [
        ["agua", "presion"],
        [r"sale agua"],
        [r"chorro de agua"],
        [r"proyeccion de agua"],
        [r"se rompio", r"aleje"],
        [r"reventon"],
    ],
    "S2": [
        [r"\bgas\b"],
    ],
    "S3": [
        [r"\bhumo\b"],
        [r"olor a quemado"],
        [r"se esta quemando"],
    ],
    "S4": [
        [r"sangr"],
        [r"lesionad"],
        [r"lastimad"],
        [r"\bherid[oa]"],
        [r"accidentad"],
        [r"golpe", r"mano"],
    ],
}


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_tildes.lower()


def detectar(texto: str) -> Deteccion | None:
    normalizado = _normalizar(texto)
    for codigo, combinaciones in _PATRONES.items():
        for fragmentos in combinaciones:
            if all(re.search(fragmento, normalizado) for fragmento in fragmentos):
                return Deteccion(codigo, _DESCRIPCIONES[codigo], _PROCEDIMIENTOS[codigo])
    return None


def construir_evento(deteccion: Deteccion, nota: NotaRecorrida) -> EventoSistema:
    return EventoSistema(
        id=f"EVT-SEGURIDAD-{nota.id}-{deteccion.codigo}",
        ts=nota.ts,
        tipo=TipoEvento.alerta_seguridad,
        origen="parte",
        nota_ids=[nota.id],
        resumen=f"{deteccion.codigo}: {deteccion.descripcion}",
    )


def construir_mensaje_tablet(deteccion: Deteccion, nota: NotaRecorrida) -> MensajeTablet:
    return MensajeTablet(
        id=str(uuid.uuid4()),
        ts=nota.ts,
        nota_ids=[nota.id],
        texto=f"ALERTA DE SEGURIDAD ({deteccion.codigo}): {deteccion.descripcion}. {deteccion.procedimiento}",
        pantalla=Pantalla(titulo=f"Alerta de seguridad {deteccion.codigo}"),
        espera_respuesta=False,
    )
