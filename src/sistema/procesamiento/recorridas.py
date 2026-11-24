import logging
import time
import wave
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


def cargar_parte(nota_id: str) -> Parte | None:
    """Para el análisis de órdenes (Fase 10): el parte confirmado que originó el evento Escalado
    ya quedó en disco vía `guardar_parte`, no hace falta volver a pedirlo."""
    archivo = config.media_dir / nota_id / "parte.json"
    if not archivo.exists():
        return None
    return Parte.model_validate_json(archivo.read_text(encoding="utf-8"))


def descargar_y_transcribir(
    nota: NotaRecorrida, http: PlantaHTTP, transcriptor: Transcriptor, tiempos: dict | None = None
) -> str:
    """Descarga el audio de la nota y lo transcribe. Se separa de `armar_parte` para que la
    detección de alerta de seguridad (2.5) corra sobre el texto apenas sale de Whisper, sin esperar
    a la extracción con LLM ni a ninguna otra llamada de red.

    Lote 6 (A5): las fotos ya no se bajan acá. Antes se bajaban todas (hasta 30 s cada una)
    **antes** de devolver el texto, o sea antes de `detectar()`: una foto lenta podía sacar la
    alerta de seguridad de los 30 s del criterio eliminatorio. Ahora van en `descargar_fotos`,
    después de la alerta y en un hilo aparte."""
    carpeta = config.media_dir / nota.id
    carpeta.mkdir(parents=True, exist_ok=True)

    texto = ""
    if nota.audio_url:
        inicio = time.perf_counter()
        audio_path = http.descargar(nota.audio_url, carpeta / "audio.wav")
        descargado = time.perf_counter()
        texto = transcriptor.transcribir(audio_path)
        if tiempos is not None:  # Lote 6 (D1): para la línea de latencia de la nota
            tiempos["descarga"] = descargado - inicio
            tiempos["whisper"] = time.perf_counter() - descargado
            tiempos["audio"] = _duracion_wav(audio_path)
        (carpeta / "transcripcion.txt").write_text(texto, encoding="utf-8")
    return texto


def _duracion_wav(ruta: Path) -> float:
    try:
        with wave.open(str(ruta), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


def descargar_fotos(nota: NotaRecorrida, http: PlantaHTTP) -> None:
    """Copia local de las fotos de la nota (registro del parte). Cada una es independiente: si
    una falla, las demás se bajan igual."""
    carpeta = config.media_dir / nota.id
    carpeta.mkdir(parents=True, exist_ok=True)
    for i, foto_url in enumerate(nota.fotos):
        try:
            http.descargar(foto_url, carpeta / f"foto_{i}.jpg")
        except Exception:
            log.exception("nota %s: no se pudo bajar la foto %d", nota.id, i)


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
    parte = Parte(id=f"PARTE-{nota.id}", nota_ids=[nota.id], estado=EstadoParte.borrador, filas=filas, texto=texto)
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
    descargar_fotos(nota, http)
    return armar_parte(nota, texto, extractor, resolver, verificador_rango)
