from pathlib import Path
from typing import Protocol

from sistema.config import config


class ModeloWhisper(Protocol):
    def transcribe(self, audio_path: str, language: str): ...


class Transcriptor:
    """Transcripción de audio con un modelo local (faster-whisper).

    Tiene que ser local: en el escenario fuga-sin-enlace el enlace se cae a los 10 minutos
    y el parte por voz tiene que seguir funcionando.
    """

    def __init__(self, modelo: ModeloWhisper | None = None) -> None:
        self._modelo = modelo

    def _cargar_modelo(self) -> ModeloWhisper:
        if self._modelo is None:
            from faster_whisper import WhisperModel

            try:
                # Si ya está cacheado localmente (de un arranque anterior), no toca la red para
                # nada: así el reinicio del agente durante un corte de enlace no se queda sin
                # transcripción esperando a Hugging Face.
                self._modelo = WhisperModel(
                    config.transcripcion_modelo, device="cpu", compute_type="int8", local_files_only=True
                )
            except Exception:
                self._modelo = WhisperModel(
                    config.transcripcion_modelo, device="cpu", compute_type="int8"
                )
        return self._modelo

    def transcribir(self, audio_path: Path) -> str:
        modelo = self._cargar_modelo()
        segmentos, _info = modelo.transcribe(str(audio_path), language="es")
        return " ".join(seg.text.strip() for seg in segmentos).strip()
