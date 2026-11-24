import threading
import time
import wave
from pathlib import Path
from typing import Protocol

from sistema.config import config


class ModeloWhisper(Protocol):
    def transcribe(self, audio_path: str, language: str, **opciones): ...


class Transcriptor:
    """Transcripción de audio con un modelo local (faster-whisper).

    Tiene que ser local: en el escenario fuga-sin-enlace el enlace se cae a los 10 minutos
    y el parte por voz tiene que seguir funcionando.

    Lote 6 (A1): hilos, beam y VAD salen de `config` (elegidos midiendo con
    `evaluacion/evaluar_whisper.py`). Con los defaults de faster-whisper (`cpu_threads=0`)
    CTranslate2 tomaba todos los núcleos del host aunque el contenedor tuviera 2.5 CPU, y en una
    máquina cargada eso se pagaba con throttling (13 s por nota de mediana en `vision-nemotron`
    contra 4.7 s en `g1c`). El modelo se carga una sola vez (con lock: una nota y un cierre que
    llegan juntos no cargan dos modelos y no pasan el límite de memoria) y se precarga al arrancar
    para que la primera nota no pague la carga.
    """

    def __init__(self, modelo: ModeloWhisper | None = None) -> None:
        self._modelo = modelo
        self._lock_carga = threading.Lock()

    def _cargar_modelo(self) -> ModeloWhisper:
        if self._modelo is not None:
            return self._modelo
        with self._lock_carga:
            if self._modelo is None:
                from faster_whisper import WhisperModel

                opciones = {"device": "cpu", "compute_type": "int8", "cpu_threads": config.whisper_hilos}
                try:
                    # Si ya está cacheado localmente (de un arranque anterior), no toca la red para
                    # nada: así el reinicio del agente durante un corte de enlace no se queda sin
                    # transcripción esperando a Hugging Face.
                    self._modelo = WhisperModel(config.transcripcion_modelo, local_files_only=True, **opciones)
                except Exception:
                    self._modelo = WhisperModel(config.transcripcion_modelo, **opciones)
        return self._modelo

    def precargar(self, carpeta_temporal: Path) -> float:
        """Carga el modelo y transcribe medio segundo de silencio (la primera transcripción paga la
        inicialización de CTranslate2). Devuelve los segundos que tardó."""
        inicio = time.perf_counter()
        carpeta_temporal.mkdir(parents=True, exist_ok=True)
        silencio = carpeta_temporal / "precarga.wav"
        with wave.open(str(silencio), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 8000)
        self.transcribir(silencio)
        return time.perf_counter() - inicio

    def transcribir(self, audio_path: Path) -> str:
        modelo = self._cargar_modelo()
        segmentos, _info = modelo.transcribe(
            str(audio_path), language="es", beam_size=config.whisper_beam, vad_filter=config.whisper_vad
        )
        return " ".join(seg.text.strip() for seg in segmentos).strip()
