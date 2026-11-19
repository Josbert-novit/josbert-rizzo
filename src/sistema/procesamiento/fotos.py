"""Resolución de fotos (Fase 13.2/13.3): o se analizan ya mismo (hay enlace), o se encolan para
cuando vuelva. Si el análisis llega después de que la orden que acompaña ya se publicó y detecta
daño estructural, reabre esa orden a `PendienteAprobacion` — nunca revierte un cierre o una
certificación ya hechos (13.3). El caso típico del anexo (`fuga-sin-enlace`) es exactamente este
camino: la orden se publica con el enlace caído (sin foto todavía), y el daño aparece recién
cuando la foto se analiza más tarde.
"""

from __future__ import annotations

import logging

from planta_contrato import EstadoOrden

from sistema.config import config
from sistema.integraciones.enlace import enlace_arriba
from sistema.llm.vision import AnalisisFoto, ClienteVision, analizar_foto
from sistema.persistencia import fotos as persistencia_fotos
from sistema.persistencia import ordenes as persistencia_ordenes

log = logging.getLogger(__name__)

# Un análisis tardío no puede revertir un cierre, una certificación o un rechazo ya decididos.
ESTADOS_NO_REABRIBLES = frozenset({EstadoOrden.cerrada, EstadoOrden.certificada, EstadoOrden.rechazada})


def reabrir_orden_si_danio_estructural(conn, orden_id: str, analisis: AnalisisFoto) -> bool:
    """`True` si la orden se reabrió. No hace nada si la orden no existe, si no hay daño, o si ya
    está en un estado que un análisis tardío no puede revertir."""
    if not analisis.danio_estructural:
        return False
    orden = persistencia_ordenes.cargar(conn, orden_id)
    if orden is None or orden.estado in ESTADOS_NO_REABRIBLES:
        return False
    reabierta = orden.model_copy(update={
        "estado": EstadoOrden.pendiente,
        "motivo": (f"daño estructural detectado en foto (análisis tardío, {analisis.componente}): "
                   f"{analisis.hallazgo or analisis.descripcion}"),
    })
    persistencia_ordenes.guardar(conn, reabierta, pendiente_publicar=True)
    return True


def _vision_disponible(cliente: ClienteVision | None) -> bool:
    """Sin cliente explícito y sin `NVIDIA_API_KEY` no hay análisis posible: la foto no puede
    quedar pendiente para siempre (el latido mostraría pendientes eternos, E2). Se registra y se
    marca resuelta sin análisis."""
    return cliente is not None or bool(config.nvidia_api_key)


def encolar_o_analizar(
    foto_id: str, url: str, orden_id: str | None = None, cliente: ClienteVision | None = None,
) -> AnalisisFoto | None:
    """Punto de entrada único para cualquier foto que necesite análisis de visión (cierres, Fase
    14; fotos de un parte). `None`: no hay enlace, la foto quedó encolada."""
    conn_fotos = persistencia_fotos.conectar()
    try:
        if not enlace_arriba():
            persistencia_fotos.encolar(conn_fotos, foto_id, url, orden_id)
            return None
        if not _vision_disponible(cliente):
            log.warning("foto %s: sin análisis (visión no configurada: falta NVIDIA_API_KEY)", foto_id)
            persistencia_fotos.encolar(conn_fotos, foto_id, url, orden_id)
            persistencia_fotos.marcar_resuelta(conn_fotos, foto_id)
            return None
        analisis = analizar_foto(url, cliente)
        persistencia_fotos.encolar(conn_fotos, foto_id, url, orden_id)
        persistencia_fotos.marcar_resuelta(conn_fotos, foto_id)
    finally:
        conn_fotos.close()

    if orden_id:
        conn_ordenes = persistencia_ordenes.conectar()
        try:
            if reabrir_orden_si_danio_estructural(conn_ordenes, orden_id, analisis):
                log.warning("orden %s reabierta por daño estructural detectado en foto %s", orden_id, foto_id)
        finally:
            conn_ordenes.close()
    return analisis


def reconciliar_fotos_pendientes(cliente: ClienteVision | None = None) -> int:
    """Reintenta las fotos encoladas (13.2). Se llama desde un hilo propio en `agente.py`, nunca
    desde el loop de red de paho-mqtt. Devuelve cuántas se resolvieron en esta vuelta."""
    if not enlace_arriba():
        return 0
    conn_fotos = persistencia_fotos.conectar()
    try:
        pendientes = persistencia_fotos.pendientes(conn_fotos)
        resueltas = 0
        for foto in pendientes:
            if not _vision_disponible(cliente):
                log.warning("foto %s: sin análisis (visión no configurada: falta NVIDIA_API_KEY)", foto["id"])
                persistencia_fotos.marcar_resuelta(conn_fotos, foto["id"])
                resueltas += 1
                continue
            try:
                analisis = analizar_foto(foto["url"], cliente)
            except Exception:
                log.exception("foto %s: falló el análisis de visión", foto["id"])
                continue
            persistencia_fotos.marcar_resuelta(conn_fotos, foto["id"])
            resueltas += 1
            if foto["orden_id"]:
                conn_ordenes = persistencia_ordenes.conectar()
                try:
                    if reabrir_orden_si_danio_estructural(conn_ordenes, foto["orden_id"], analisis):
                        log.warning(
                            "orden %s reabierta por daño estructural detectado en foto %s",
                            foto["orden_id"], foto["id"],
                        )
                finally:
                    conn_ordenes.close()
        return resueltas
    finally:
        conn_fotos.close()
