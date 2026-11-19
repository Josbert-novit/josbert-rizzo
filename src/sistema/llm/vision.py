"""Visión multimodal (Fase 13): análisis de fotos (de un cierre, o adjuntas a un parte) con un
modelo de NVIDIA NIM — API compatible OpenAI (`https://integrate.api.nvidia.com/v1`). No hay un
modelo local liviano equivalente a Whisper/qwen2.5/nomic-embed-text para visión multimodal (ver
Decisión de diseño #3 en `PLAN.md`), así que acá sí se usa la nube; por eso mismo el análisis
nunca puede ser una dependencia dura del camino crítico (ver `persistencia/fotos.py` y
`procesamiento/fotos.py` para la cola que lo resuelve cuando no hay enlace).
"""

from __future__ import annotations

import json
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from sistema.config import config

BASE_URL_NVIDIA = "https://integrate.api.nvidia.com/v1"

PROMPT = """Mirá esta foto de un equipo de una planta de inyección de agua y respondé ÚNICAMENTE \
con un JSON de la forma {"danio_estructural": bool, "descripcion": str}.

"danio_estructural" es true solo si hay evidencia visual clara de daño estructural (grietas, \
corrosión severa, deformación del cuerpo del equipo o la cañería, fuga activa sobre una \
estructura) — no por suciedad, pintura descascarada o desgaste cosmético normal. "descripcion" \
es una frase corta de qué se ve en la foto, en español."""


class AnalisisFoto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    danio_estructural: bool
    descripcion: str


class ClienteVision(Protocol):
    def analizar(self, url: str) -> AnalisisFoto: ...


class ClienteNvidiaNIM:
    def __init__(
        self, api_key: str | None = None, modelo: str | None = None, base_url: str = BASE_URL_NVIDIA,
    ) -> None:
        self._api_key = api_key if api_key is not None else config.nvidia_api_key
        self._modelo = modelo if modelo is not None else config.nvidia_modelo_vision
        self._base_url = base_url

    def analizar(self, url: str) -> AnalisisFoto:
        respuesta = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._modelo,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }],
                "temperature": 0,
                "max_tokens": 300,
            },
            timeout=30,
        )
        respuesta.raise_for_status()
        contenido = respuesta.json()["choices"][0]["message"]["content"]
        return AnalisisFoto.model_validate(json.loads(contenido))


def analizar_foto(url: str, cliente: ClienteVision | None = None) -> AnalisisFoto:
    cliente = cliente or ClienteNvidiaNIM()
    return cliente.analizar(url)
