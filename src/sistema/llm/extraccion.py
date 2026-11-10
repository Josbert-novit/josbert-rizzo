from typing import Protocol

from planta_contrato import FilaParte
from pydantic import BaseModel, ConfigDict, Field

from sistema.config import config
from sistema.llm.control import ConsumoTokens, config_de_callback

PROMPT_SISTEMA = """Sos un asistente que convierte notas de voz de un operador de una planta de \
inyección de agua en filas estructuradas de un parte de mantenimiento.

Una nota puede mezclar varios equipos y datos en cualquier orden. Para cada equipo y variable que \
el operador menciona, generá una fila con:
- tag: el identificador o nombre del equipo tal como lo dice el operador, incluso si es informal \
(por ejemplo "la dos", "el filtro de la izquierda"). No lo resuelvas contra un listado: eso lo hace \
otro paso.
- variable: qué midió u observó (por ejemplo presión de descarga, diferencial, temperatura, vibración).
- valor: el número mencionado, si lo hay.
- unidad: la unidad mencionada o implícita por el equipo (kg/cm2, psi, bar), o null si no se puede \
inferir con certeza.
- observacion: cualquier comentario cualitativo (ruido, pérdida, olor, etc.), o null si no hay.

No inventes datos que la nota no menciona. Si falta un dato, dejalo null. Una nota sin equipos ni \
variables (por ejemplo una nota en vivo sin nada para reportar) produce una lista de filas vacía."""


class FilasExtraidas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filas: list[FilaParte] = Field(default_factory=list)


class ChatEstructurado(Protocol):
    def invoke(self, mensajes: list[tuple[str, str]], config: dict | None = None) -> FilasExtraidas: ...


class Extractor:
    """Extrae filas de parte desde el texto transcripto de una nota de voz, con un LLM local."""

    def __init__(self, chat: ChatEstructurado | None = None) -> None:
        self._chat = chat

    def _cargar_chat(self) -> ChatEstructurado:
        if self._chat is None:
            from langchain_ollama import ChatOllama

            self._chat = ChatOllama(
                model=config.llm_modelo_extraccion,
                base_url=config.ollama_url,
                temperature=0,
            ).with_structured_output(FilasExtraidas)
        return self._chat

    def extraer(self, texto: str, consumo: ConsumoTokens | None = None) -> FilasExtraidas:
        if not texto.strip():
            return FilasExtraidas(filas=[])
        chat = self._cargar_chat()
        mensajes = [("system", PROMPT_SISTEMA), ("human", texto)]
        if consumo is None:
            return chat.invoke(mensajes)
        return chat.invoke(mensajes, config=config_de_callback(consumo))
