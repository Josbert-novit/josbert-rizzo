import logging
from pathlib import Path

from planta_contrato import NotaRecorrida

from sistema.config import config
from sistema.dominio.parte import EstadoParte, Parte
from sistema.integraciones.planta_http import PlantaHTTP
from sistema.llm.control import ConsumoTokens
from sistema.llm.extraccion import Extractor
from sistema.llm.transcripcion import Transcriptor
from sistema.procesamiento.interpretacion import interpretar_texto
from sistema.procesamiento.rango_historico import VerificadorRango
from sistema.procesamiento.resolucion import ResolverNombres

log = logging.getLogger(__name__)


def guardar_parte(nota_id: str, parte: Parte) -> Path:
    carpeta = config.media_dir / nota_id
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "parte.json").write_text(parte.model_dump_json(indent=2), encoding="utf-8")
    return carpeta


def descargar_y_transcribir(nota: NotaRecorrida, http: PlantaHTTP, transcriptor: Transcriptor) -> str:
    """Descarga audio y fotos de la nota y transcribe el audio. Se separa de `armar_parte` para
    que la detección de alerta de seguridad (2.5) corra sobre el texto apenas sale de Whisper,
    sin esperar a la extracción con LLM ni a ninguna otra llamada de red."""
    carpeta = config.media_dir / nota.id
    carpeta.mkdir(parents=True, exist_ok=True)

    texto = ""
    if nota.audio_url:
        audio_path = http.descargar(nota.audio_url, carpeta / "audio.wav")
        texto = transcriptor.transcribir(audio_path)
        (carpeta / "transcripcion.txt").write_text(texto, encoding="utf-8")

    for i, foto_url in enumerate(nota.fotos):
        http.descargar(foto_url, carpeta / f"foto_{i}.jpg")

    return texto


def armar_parte(
    nota: NotaRecorrida,
    texto: str,
    extractor: Extractor,
    resolver: ResolverNombres,
    verificador_rango: VerificadorRango,
    consumo: ConsumoTokens | None = None,
) -> Parte:
    """Arma un parte en borrador a partir del texto ya transcripto: extracción (1.3), resolución
    de nombres (1.4), unidades (1.5) y rango histórico (1.6).

    Las notas en vivo (`id` empieza con 'VIVO-') pueden no traer nada que reportar.

    Simplificación de esta etapa: todas las fotos de la nota se asocian a todas las filas
    extraídas (la asociación fila por fila queda para un paso posterior).
    """
    filas = interpretar_texto(texto, nota.ts, nota.fotos, extractor, resolver, verificador_rango, consumo)
    parte = Parte(id=f"PARTE-{nota.id}", nota_ids=[nota.id], estado=EstadoParte.borrador, filas=filas)
    guardar_parte(nota.id, parte)

    sin_resolver = sum(1 for f in parte.filas if f.tag is None)
    log.info(
        "nota %s: audio=%s fotos=%d transcripcion=%r filas=%d sin_resolver=%d",
        nota.id,
        bool(nota.audio_url),
        len(nota.fotos),
        texto,
        len(parte.filas),
        sin_resolver,
    )
    return parte


def recibir_nota(
    nota: NotaRecorrida,
    http: PlantaHTTP,
    transcriptor: Transcriptor,
    extractor: Extractor,
    resolver: ResolverNombres,
    verificador_rango: VerificadorRango,
) -> Parte:
    texto = descargar_y_transcribir(nota, http, transcriptor)
    return armar_parte(nota, texto, extractor, resolver, verificador_rango)
