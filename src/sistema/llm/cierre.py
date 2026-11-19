"""Extracción de una nota de cierre (Fase 14.2): mismo patrón que `llm/extraccion.py` (LLM local,
`ChatEstructurado` inyectable para tests) pero para el texto transcripto de un cierre en vez de
una recorrida — es un dominio distinto (tareas realizadas, repuestos declarados, horas), así que
es un extractor aparte en vez de forzar `Extractor`/`FilasExtraidas` a servir para dos cosas.
"""

import re
from typing import Protocol

from planta_contrato import Repuesto
from pydantic import BaseModel, ConfigDict, Field

from sistema.config import config
from sistema.llm.control import ConsumoTokens, config_de_callback

PROMPT_SISTEMA = """Sos un asistente que convierte el relato de un técnico contratista cerrando \
una orden de trabajo de mantenimiento en datos estructurados.

A partir del texto, extraé:
- tareas: los códigos de tarea que el técnico dice haber hecho (p. ej. "T-B-ROD", "T-B-ALI", \
"I-VIB"), identificados por la descripción de la tarea en el texto (cambio de rodamientos del \
cigüeñal = T-B-ROD, alineación láser de acople = T-B-ALI, análisis de vibraciones con colector = \
I-VIB).
- repuestos: cada repuesto que diga haber usado, con su código del catálogo y la cantidad.
- horas: las horas que dice haber trabajado, si las menciona.
- resultado_prueba: la frase con el resultado de la prueba final, tal como la dice (p. ej. "probé a ciento diez kilos, sin pérdida"), o null si no menciona ninguna prueba.

No inventes nada que el texto no diga. Si no se menciona una hora, dejala null."""


class CierreExtraido(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tareas: list[str] = Field(default_factory=list)
    repuestos: list[Repuesto] = Field(default_factory=list)
    horas: float | None = None
    resultado_prueba: str | None = None


_PATRON_PRUEBA = re.compile(r"\bprob[ée]\b[^.]*", re.IGNORECASE)


def resultado_prueba_del_texto(texto: str) -> str | None:
    """Respaldo determinístico para el resultado de la prueba ("Probé a 110 kilos, sin pérdida."):
    si el LLM no lo extrae, se toma la oración literal que empieza con "probé"."""
    m = _PATRON_PRUEBA.search(texto or "")
    return m.group(0).strip() if m else None


class ChatEstructurado(Protocol):
    def invoke(self, mensajes: list[tuple[str, str]], config: dict | None = None) -> CierreExtraido: ...


class ExtractorCierre:
    def __init__(self, chat: ChatEstructurado | None = None) -> None:
        self._chat = chat

    def _cargar_chat(self) -> ChatEstructurado:
        if self._chat is None:
            from langchain_ollama import ChatOllama

            self._chat = ChatOllama(
                model=config.llm_modelo_extraccion, base_url=config.ollama_url, temperature=0,
            ).with_structured_output(CierreExtraido)
        return self._chat

    def extraer(self, texto: str, consumo: ConsumoTokens | None = None) -> CierreExtraido:
        if not texto.strip():
            return CierreExtraido()
        chat = self._cargar_chat()
        mensajes = [("system", PROMPT_SISTEMA), ("human", texto)]
        if consumo is None:
            cierre = chat.invoke(mensajes)
        else:
            cierre = chat.invoke(mensajes, config=config_de_callback(consumo))
        if not cierre.resultado_prueba:
            respaldo = resultado_prueba_del_texto(texto)
            if respaldo:
                cierre = cierre.model_copy(update={"resultado_prueba": respaldo})
        return cierre
