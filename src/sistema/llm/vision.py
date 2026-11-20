"""Visión multimodal (Fase 13): análisis de fotos (de un cierre, o adjuntas a un parte) con un
modelo de NVIDIA NIM — API compatible OpenAI (`https://integrate.api.nvidia.com/v1`). No hay un
modelo local liviano equivalente a Whisper/qwen2.5/nomic-embed-text para visión multimodal (ver
Decisión de diseño #3 en `PLAN.md`), así que acá sí se usa la nube; por eso mismo el análisis
nunca puede ser una dependencia dura del camino crítico (ver `persistencia/fotos.py` y
`procesamiento/fotos.py` para la cola que lo resuelve cuando no hay enlace).
"""

from __future__ import annotations

import base64
import json
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from sistema.config import config

BASE_URL_NVIDIA = "https://integrate.api.nvidia.com/v1"

PROMPT = """Mirá esta foto de un equipo de una planta de inyección de agua (bombas de émbolos TX-350, filtros de cartuchos, cañerías) y respondé ÚNICAMENTE con un JSON de la forma:
{"componente": str, "estado": str, "hallazgo": str, "lectura_manometro": number|null, "danio_estructural": bool, "descripcion": str}

- "componente": el principal que se ve. Uno de: "empaquetadura", "correas", "acople", "manometro", "filtro", "otro".
- "estado": "normal", "desgaste" o "falla".
- "hallazgo": qué se ve en ese componente, según el caso: empaquetadura (goteo, chorro, pérdida), correas (cuarteadas, flojas, cortadas, patinan), acople (elastómero roto, desalineación, polvo de goma), manómetro (la aguja: valor y unidad si se lee), filtro (cartuchos colmatados, rotos, carcasa con pérdida).
- "lectura_manometro": el valor que marca la aguja si el componente es un manómetro y se lee con claridad; si no, null.
- "danio_estructural": true SOLO si hay evidencia visual clara de daño estructural grave (grietas, rotura o deformación del cuerpo del equipo, la base o la cañería, corrosión que compromete la pared, fuga activa sobre una estructura). No por suciedad, pintura descascarada, desgaste normal o un goteo de empaquetadura.
- "descripcion": una frase corta en español de qué se ve."""


COMPONENTES = ("empaquetadura", "correas", "acople", "manometro", "filtro", "otro")


class AnalisisFoto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    danio_estructural: bool
    descripcion: str
    componente: Literal["empaquetadura", "correas", "acople", "manometro", "filtro", "otro"] = "otro"
    estado: Literal["normal", "desgaste", "falla"] | None = None
    hallazgo: str | None = None
    lectura_manometro: float | None = None

    @field_validator("componente", mode="before")
    @classmethod
    def _normalizar_componente(cls, v):
        v = str(v or "otro").strip().lower().replace("ó", "o")
        return v if v in COMPONENTES else "otro"


def _extraer_json(contenido: str) -> dict:
    """Los modelos de visión a veces envuelven el JSON en ```json ... ``` o agregan texto."""
    inicio, fin = contenido.find("{"), contenido.rfind("}")
    if inicio == -1 or fin == -1:
        raise ValueError(f"la respuesta de visión no trae JSON: {contenido[:120]!r}")
    return json.loads(contenido[inicio:fin + 1])


def imagen_como_data_uri(url: str, base_planta: str | None = None, get=httpx.get) -> str:
    """La foto vive en la planta (`/data/notas/...`, red interna `planta-lan`): la nube de NVIDIA
    no puede ir a buscarla. Se baja acá (sin proxy, `NO_PROXY=sim`) y se manda embebida."""
    if url.startswith("data:"):
        return url
    absoluta = url if url.startswith("http") else f"{(base_planta or config.historian_url).rstrip('/')}{url}"
    r = get(absoluta, timeout=30)
    r.raise_for_status()
    tipo = r.headers.get("content-type", "image/jpeg").split(";")[0]
    return f"data:{tipo};base64,{base64.b64encode(r.content).decode('ascii')}"


class ClienteVision(Protocol):
    def analizar(self, url: str) -> AnalisisFoto: ...


class ClienteNvidiaNIM:
    def __init__(
        self, api_key: str | None = None, modelo: str | None = None, base_url: str = BASE_URL_NVIDIA,
    ) -> None:
        self._api_key = api_key if api_key is not None else config.nvidia_api_key
        self._modelo = modelo if modelo is not None else config.nvidia_modelo_vision
        self._base_url = base_url

    def _cuerpo(self, url: str) -> dict:
        cuerpo = {
            "model": self._modelo,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": imagen_como_data_uri(url)}},
                ],
            }],
            "temperature": 0,
            "max_tokens": 400,
        }
        if "nemotron" in self._modelo:
            # Nemotron VL razona antes de contestar: sin esto el razonamiento (~600 tokens) se come
            # max_tokens y `content` llega vacío. Apagado responde en ~2 s con ~50 tokens.
            cuerpo["chat_template_kwargs"] = {"enable_thinking": False}
        return cuerpo

    def analizar(self, url: str) -> AnalisisFoto:
        respuesta = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=self._cuerpo(url),
            timeout=60,
        )
        respuesta.raise_for_status()
        contenido = respuesta.json()["choices"][0]["message"].get("content")
        if not contenido:
            raise ValueError(f"el modelo de visión {self._modelo} no devolvió contenido")
        return AnalisisFoto.model_validate(_extraer_json(contenido))


def analizar_foto(url: str, cliente: ClienteVision | None = None) -> AnalisisFoto:
    cliente = cliente or ClienteNvidiaNIM()
    return cliente.analizar(url)
