import logging
from pathlib import Path

from planta_contrato import NotaRecorrida

from sistema.config import config
from sistema.integraciones.planta_http import PlantaHTTP

log = logging.getLogger(__name__)


def recibir_nota(nota: NotaRecorrida, http: PlantaHTTP) -> Path:
    """Descarga el audio y las fotos de una nota de recorrida a disco local.

    Las notas en vivo (`id` empieza con 'VIVO-') pueden no traer nada que reportar,
    pero igual se descargan como cualquier otra nota.
    """
    carpeta = config.media_dir / nota.id
    carpeta.mkdir(parents=True, exist_ok=True)

    if nota.audio_url:
        http.descargar(nota.audio_url, carpeta / "audio.wav")

    for i, foto_url in enumerate(nota.fotos):
        http.descargar(foto_url, carpeta / f"foto_{i}.jpg")

    log.info("nota %s: audio=%s fotos=%d", nota.id, bool(nota.audio_url), len(nota.fotos))
    return carpeta
