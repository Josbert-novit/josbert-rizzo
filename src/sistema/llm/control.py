"""Presupuesto de pasos y tokens por nota (4.1).

Cada nota (incluidas sus correcciones durante el diálogo de confirmación, 1.7) puede disparar
varias llamadas al LLM de extracción. Sin un tope, una nota mal interpretada que el operador
nunca termina de confirmar podría generar un ida-y-vuelta indefinido. `ConsumoTokens` cuenta
pasos y tokens reales (de `generation_info` de Ollama, vía callback de LangChain — no una
estimación) por nota; `verificar_presupuesto` corta el ciclo y escala a revisión humana en vez de
seguir preguntando.

El consumo también sirve para el INFORME.md (Fase 6.2): como el modelo es local, no hay costo en
USD, pero sí tiene sentido reportar cuántos tokens/pasos insume interpretar una nota típica.
"""

import threading
import time
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler

MAX_PASOS_LLM_POR_NOTA = 6  # 1 extracción inicial + hasta 5 correcciones del operador

# Prioridad de Ollama para el análisis de órdenes (Entrega 2, bug #27): Ollama en esta CPU (sin
# GPU) sirve un pedido a la vez, así que aunque notas y órdenes corran en pools de Python
# distintos, siguen compitiendo por el mismo servidor. D1/D2/F3/G1 (30 puntos de la rúbrica) vienen
# de que una orden termine de analizarse; una nota de voz individual pesa mucho menos — así que,
# mientras haya un análisis de orden en curso (`agente.py::_procesar_evento_escalado`), una nota
# cede el turno antes de mandar su propia llamada al LLM. Tope defensivo de esa espera: nunca
# dejar una nota colgada para siempre si este evento quedara prendido por algún bug.
#
# **Revertido en la Fase 15 (hallazgo H8)**: medido contra el corrector real (corrida
# lote1-contratista), ceder el turno hasta 60 s *por cada llamada* de una nota (y una nota hace
# hasta 6) la llevaba por encima de su corte de 90 s: 13 de 19 notas cortadas mientras un único
# análisis de orden estaba colgado. Además el supuesto "Ollama sirve un pedido a la vez" no se
# cumplió: con `OLLAMA_NUM_PARALLEL` en automático atendió notas en paralelo con el análisis. Los
# extractores (`extraccion.py`, `cierre.py`) ya no llaman a `ceder_turno_si_hay_orden_en_curso`;
# el análisis ahora está acotado por sí mismo (`llm/analisis.py`: num_predict, timeout). Se
# mantienen el `Event` (lo usa `agente.py`) y la función (cubierta por `tests/test_control.py`).
analisis_orden_en_curso = threading.Event()
ESPERA_MAXIMA_PRIORIDAD_ORDEN_S = 60.0


def ceder_turno_si_hay_orden_en_curso() -> None:
    esperado = 0.0
    while analisis_orden_en_curso.is_set() and esperado < ESPERA_MAXIMA_PRIORIDAD_ORDEN_S:
        time.sleep(0.5)
        esperado += 0.5


class TrabajoUrgente:
    """Cuenta los trabajos con apuro en curso (nota, respuesta del operador, análisis de orden,
    cierre) — Entrega 2, hallazgo H1 de la Fase 15. Mientras haya al menos uno, la indexación de
    embeddings en segundo plano (`rag/indexar.py`) no manda su próximo lote a Ollama.

    Contador y no un `Event`: notas y órdenes corren en pools distintos y pueden solaparse; con un
    `Event`, el primero que termina lo apagaría aunque el otro siga corriendo. Antes la indexación
    solo se pausaba durante un análisis de orden, y en el corrector real (corrida v6) 28 de 45
    notas se cortaron a los 90 s: competían por el Ollama de CPU contra 701 embeddings."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._en_curso = 0

    def __enter__(self) -> "TrabajoUrgente":
        with self._lock:
            self._en_curso += 1
        return self

    def __exit__(self, *_exc) -> None:
        with self._lock:
            self._en_curso -= 1

    def activo(self) -> bool:
        with self._lock:
            return self._en_curso > 0


trabajo_urgente = TrabajoUrgente()


class PresupuestoAgotado(Exception):
    """Se superó el presupuesto de pasos de LLM para esta nota."""


@dataclass
class ConsumoTokens:
    pasos: int = 0
    tokens_prompt: int = 0
    tokens_respuesta: int = 0

    def registrar(self, prompt: int, respuesta: int) -> None:
        self.pasos += 1
        self.tokens_prompt += prompt
        self.tokens_respuesta += respuesta

    @property
    def tokens_totales(self) -> int:
        return self.tokens_prompt + self.tokens_respuesta


class _CallbackConsumo(BaseCallbackHandler):
    def __init__(self, consumo: ConsumoTokens) -> None:
        self._consumo = consumo

    def on_llm_end(self, response, **kwargs) -> None:
        for generations in response.generations:
            for g in generations:
                info = getattr(g, "generation_info", None) or {}
                self._consumo.registrar(info.get("prompt_eval_count", 0) or 0, info.get("eval_count", 0) or 0)


def config_de_callback(consumo: ConsumoTokens) -> dict:
    """`config=` para pasarle a `Runnable.invoke(...)` y que registre el consumo de esta llamada."""
    return {"callbacks": [_CallbackConsumo(consumo)]}


def verificar_presupuesto(consumo: ConsumoTokens) -> None:
    if consumo.pasos >= MAX_PASOS_LLM_POR_NOTA:
        raise PresupuestoAgotado(
            f"se alcanzaron las {consumo.pasos} llamadas al LLM (máximo {MAX_PASOS_LLM_POR_NOTA}) para esta nota"
        )
