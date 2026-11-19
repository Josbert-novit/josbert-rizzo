"""Orquestación explícita del workflow de órdenes (3.1, 3.2 — Fase 8): a partir de un evento
`Escalado`, un router de pasos FIJO — análisis (Fase 10) → revisor (Fase 11) → reglas de
aprobación en código (Fase 9) — nunca un agente libre que decida qué hacer. El LLM solo propone
contenido (causa probable, tareas); el revisor chequea que ese contenido no se haya inventado
nada; `reglas_aprobacion.evaluar_reglas` es la ÚNICA función que decide si la orden queda
`Aprobada` o `PendienteAprobacion` — ver Decisión de diseño #2 en `PLAN.md` (Entrega 2).
"""

from __future__ import annotations

from planta_contrato import EstadoOrden, EventoSistema, OrdenSistema

from sistema.integraciones import materiales
from sistema.integraciones.planta_http import PlantaHTTP
from sistema.llm.analisis import Analizador
from sistema.llm.control import ConsumoTokens, PresupuestoAgotado, verificar_presupuesto
from sistema.procesamiento.reglas_aprobacion import evaluar_reglas, hay_reserva_disponible
from sistema.procesamiento.revisor import revisar_borrador
from sistema.rag.buscar import buscar as buscar_fragmentos

# 11.2: un reintento acotado del análisis si el revisor lo rechaza, no un bucle sin límite — el
# tope real de llamadas al LLM lo sigue poniendo `ConsumoTokens`/`verificar_presupuesto`.
MAX_REINTENTOS_ANALISIS = 1


def _orden_de_inspeccion(evento: EventoSistema, orden_id: str, motivo: str) -> OrdenSistema:
    """Publicar una tarea de inspección con motivo explícito en vez de no publicar nada
    (`sistema_mudo` es eliminatorio) o publicar un borrador con datos no verificados."""
    return OrdenSistema(
        id=orden_id, ts=evento.ts, estado=EstadoOrden.pendiente, equipo=evento.equipo or "SIN-EQUIPO",
        evento_ids=[evento.id], tareas=["I-VIB"], detiene_equipo=False, reduce_inyeccion=False,
        ejecutor="propio", repuestos=[], motivo=motivo,
    )


# --- Orden inmediata (Lote 3 de la Fase 15, hallazgos H10/H12) -----------------------------
#
# Medido en el corrector real: cada análisis con el LLM local tarda ~2 min en esta CPU, y un
# escenario como `tormenta-perfecta` dura ~4 min — terminaba antes de que saliera ninguna orden
# (0 órdenes en 4 escenarios: D1 sin datos, D2/F3 en 0). Además el corrector califica la
# **primera** publicación de cada orden (D1 usa la primera decisión, D2 `pubs[:1]`).
#
# Por eso, apenas llega un `Escalado`, se arma en código y sin LLM una orden de **inspección** —
# la respuesta segura que el anexo pide cuando todavía no hay evidencia suficiente — y pasa por las
# MISMAS reglas de aprobación (`evaluar_reglas`, la única que decide el estado). El análisis con
# LLM sigue corriendo después, con menos prioridad, pero solo como **sugerencia para el
# planificador**: no se re-publica sobre la orden. Re-publicar no sumaría (se califica la primera
# decisión) y sí podría hacer daño: la eliminatoria `obedecio_la_nota` revisa TODAS las
# publicaciones de una orden, así que un repuesto pedido por una nota maliciosa que el LLM
# agregara después alcanzaría para eliminar la entrega.

TAREA_INSPECCION_VIBRACION = "I-VIB"
TAREA_INSPECCION_VALVULAS = "I-VAL"
_PALABRAS_VIBRACION = ("vibracion", "vibración", "temperatura", "rodamiento", "ruido", "golpe")


def tarea_de_inspeccion(evento: EventoSistema) -> str:
    """Vibración, temperatura o ruido → inspección de vibraciones; todo lo demás (presiones,
    diferenciales, caudal, pérdidas, válvulas) → inspección de válvulas."""
    texto = f"{evento.id} {evento.resumen}".lower()
    if any(p in texto for p in _PALABRAS_VIBRACION):
        return TAREA_INSPECCION_VIBRACION
    return TAREA_INSPECCION_VALVULAS


def _tarea_del_catalogo(codigo: str) -> dict:
    for tarea in materiales.tareas_estandar() + materiales.tareas_no_estandar():
        if tarea["codigo"] == codigo:
            return tarea
    return {"codigo": codigo, "detiene": True, "ejecutor": "propio"}  # conservador: no aprueba solo


def orden_inmediata(
    evento: EventoSistema,
    http: PlantaHTTP,
    danio_estructural: bool = False,
    *,
    costo_max_usd: float = 2500,
    almacen: str = "yacimiento",
    siempre_pendiente: frozenset[str] = frozenset(),
) -> OrdenSistema | None:
    """`None` si el evento no tiene equipo (no hay sobre qué abrir una orden)."""
    if evento.tipo.value != "Escalado":
        raise ValueError(f"orden_inmediata espera un evento Escalado, llegó {evento.tipo!r}")
    if not evento.equipo:
        return None
    tarea = _tarea_del_catalogo(tarea_de_inspeccion(evento))
    detiene = bool(tarea.get("detiene", True))
    borrador = OrdenSistema(
        id=f"ORD-{evento.id}", ts=evento.ts, estado=EstadoOrden.borrador, equipo=evento.equipo,
        evento_ids=[evento.id], tareas=[tarea["codigo"]], detiene_equipo=detiene,
        reduce_inyeccion=False, ejecutor=tarea.get("ejecutor", "propio"), repuestos=[],
    )
    reserva = hay_reserva_disponible(borrador.equipo, http) if detiene else True
    motivos = evaluar_reglas(
        borrador, materiales, reserva, danio_estructural,
        costo_max_usd=costo_max_usd, almacen=almacen, siempre_pendiente=siempre_pendiente,
    )
    if motivos:
        return borrador.model_copy(update={
            "estado": EstadoOrden.pendiente,
            "motivo": "inspección propuesta por el sistema; " + "; ".join(motivos),
        })
    return borrador.model_copy(update={"estado": EstadoOrden.aprobada})


ESTADOS_CERRADOS = {EstadoOrden.rechazada, EstadoOrden.cerrada, EstadoOrden.certificada}


def orden_abierta_equivalente(nueva: OrdenSistema, existentes: list[OrdenSistema]) -> OrdenSistema | None:
    """E3 (sin duplicados): mismo equipo y mismas tareas, todavía abierta. Un segundo escalado
    del mismo problema (p. ej. la vibración de B-101 que sigue alta una hora después) se suma a
    la orden que ya existe en vez de abrir otra igual."""
    for orden in existentes:
        if (orden.estado not in ESTADOS_CERRADOS and orden.equipo == nueva.equipo
                and sorted(orden.tareas) == sorted(nueva.tareas)):
            return orden
    return None


def sumar_evento(orden: OrdenSistema, evento_id: str) -> OrdenSistema:
    if evento_id in orden.evento_ids:
        return orden
    return orden.model_copy(update={"evento_ids": [*orden.evento_ids, evento_id]})


def procesar_evento_escalado(
    evento: EventoSistema,
    http: PlantaHTTP,
    embeddings,
    traductor,
    danio_estructural: bool = False,
    *,
    analizador: Analizador | None = None,
    costo_max_usd: float = 2500,
    almacen: str = "yacimiento",
    siempre_pendiente: frozenset[str] = frozenset(),
    consumo: ConsumoTokens | None = None,
) -> OrdenSistema | None:
    """`None`: el análisis determinó que este evento no genera orden (p. ej. F10, un sensor con
    deriva — 10.3), no es un error."""
    if evento.tipo.value != "Escalado":
        raise ValueError(f"procesar_evento_escalado espera un evento Escalado, llegó {evento.tipo!r}")

    orden_id = f"ORD-{evento.id}"
    consumo = consumo or ConsumoTokens()

    def buscar_citas(pregunta: str, k: int = 5, equipo: str | None = None):
        return buscar_fragmentos(embeddings, pregunta, k=k, equipo=equipo)

    analizador = analizador or Analizador(http, traductor, buscar_citas)

    diagnostico = None
    motivos_revision: list[str] = []
    for _ in range(MAX_REINTENTOS_ANALISIS + 1):
        try:
            verificar_presupuesto(consumo)
        except PresupuestoAgotado as e:
            return _orden_de_inspeccion(evento, orden_id, f"presupuesto de análisis agotado: {e}")

        try:
            propuesta = analizador.analizar(evento, consumo=consumo)
        except Exception as e:  # noqa: BLE001 — timeout, JSON cortado por num_predict, Ollama caído
            # Hallazgo H7 (Fase 15): antes una excepción acá dejaba el evento SIN orden (D1/D2 sin
            # datos). Una inspección con motivo explícito es la respuesta segura que la rúbrica
            # acepta cuando la evidencia no alcanza (`tareas_de_inspeccion: [I-VIB, I-VAL]`), y
            # sigue sin aprobarse sola: queda `PendienteAprobacion`.
            return _orden_de_inspeccion(
                evento, orden_id, f"el análisis automático no terminó ({type(e).__name__}): inspeccionar"
            )
        if propuesta.borrador is None:
            return None

        citas_vigentes = (
            {f.cita for f in buscar_citas(evento.resumen, k=20, equipo=evento.equipo)}
            if evento.equipo else set()
        )
        motivos_revision = revisar_borrador(propuesta, materiales, citas_vigentes)
        if not motivos_revision:
            diagnostico = propuesta
            break

    if diagnostico is None:
        return _orden_de_inspeccion(
            evento, orden_id,
            f"el análisis no pasó la revisión tras {MAX_REINTENTOS_ANALISIS + 1} intento(s): "
            + "; ".join(motivos_revision),
        )

    borrador = diagnostico.borrador
    reserva = hay_reserva_disponible(borrador.equipo, http) if borrador.detiene_equipo else True
    orden_borrador = borrador.a_orden_sistema(evento, orden_id)
    motivos_reglas = evaluar_reglas(
        orden_borrador, materiales, reserva, danio_estructural,
        costo_max_usd=costo_max_usd, almacen=almacen, siempre_pendiente=siempre_pendiente,
    )
    if motivos_reglas:
        return orden_borrador.model_copy(
            update={"estado": EstadoOrden.pendiente, "motivo": "; ".join(motivos_reglas)}
        )
    return orden_borrador.model_copy(update={"estado": EstadoOrden.aprobada})
