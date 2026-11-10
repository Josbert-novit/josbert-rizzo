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

from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler

MAX_PASOS_LLM_POR_NOTA = 6  # 1 extracción inicial + hasta 5 correcciones del operador


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
