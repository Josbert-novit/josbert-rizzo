"""Orquestación explícita del workflow de órdenes (3.1, 3.2 — Fase 8): a partir de un evento
`Escalado`, un router de pasos FIJO — análisis (Fase 10) → revisor (Fase 11) → reglas de
aprobación en código (Fase 9) — nunca un agente libre que decida qué hacer. El LLM solo propone
contenido (causa probable, tareas); el revisor chequea que ese contenido no se haya inventado
nada; `reglas_aprobacion.evaluar_reglas` es la ÚNICA función que decide si la orden queda
`Aprobada` o `PendienteAprobacion` — ver Decisión de diseño #2 en `PLAN.md` (Entrega 2).
"""

from __future__ import annotations

from planta_contrato import EstadoOrden, EventoSistema, OrdenSistema, Repuesto

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


def _tarea_del_catalogo(codigo: str, equipo: str | None = None) -> dict:
    # T-F-CAR aparece dos veces en el catálogo (F-201A con FC-CART-25, F-201B con FC-CART-10):
    # con el equipo se elige la entrada que le corresponde.
    candidatas = [t for t in materiales.tareas_estandar() + materiales.tareas_no_estandar() if t["codigo"] == codigo]
    for tarea in candidatas:
        if equipo is None or equipo in tarea.get("equipos", [equipo]):
            return tarea
    if candidatas:
        return candidatas[0]
    return {"codigo": codigo, "detiene": True, "ejecutor": "propio"}  # conservador: no aprueba solo


# --- Tarea estándar cuando la evidencia es clara (Anexo C, G1) -------------------------------
#
# El anexo: "Si la evidencia NO alcanza, la acción es una inspección (I-VIB o I-VAL)". Cuando sí
# alcanza (el operador dice "pierde por la empaquetadura" o el sensor de diferencial de un filtro
# está en alarma sostenida), corresponde la tarea real del catálogo, y la decisión de aprobarla
# sigue siendo SOLO de `evaluar_reglas`. Se limita a fallas con una única tarea posible y sin otra
# señal que la contradiga; ante cualquier mezcla (otra novedad, valores fuera de límite) se vuelve
# a la inspección. Mapeo falla → tarea tomado del catálogo de la planta (`catalogo.py::resuelve`):
# F01 empaquetadura → T-B-EMP, F04 aceite → T-B-ACE, F05 correas → T-B-COR, F07/F08 cartuchos → T-F-CAR.
TAREA_POR_NOVEDAD = {
    "pérdida por empaquetadura": "T-B-EMP",
    "nivel de aceite bajo": "T-B-ACE",
    "correas en mal estado": "T-B-COR",
    "diferencial alto": "T-F-CAR",
}
# "pierde" sale junto con "empaquetadura" en casi toda nota de F01: no la contradice.
_NOVEDADES_COMPATIBLES = {"T-B-EMP": {"pérdida"}}
_PREFIJO_NOVEDAD = "El operador informa en "


def _novedades_del_resumen(resumen: str) -> list[str] | None:
    """Las novedades dichas que trae el resumen de un Escalado por parte. `None` si el resumen
    trae además otra cosa (p. ej. valores fuera de límite): evidencia mezclada."""
    partes = [p.strip() for p in resumen.split(". ") if p.strip()]
    novedades, otras = [], []
    for parte in partes:
        if parte.startswith(_PREFIJO_NOVEDAD) and ":" in parte:
            novedades += [n.strip().rstrip(".") for n in parte.split(":", 1)[1].split(",") if n.strip()]
        else:
            otras.append(parte)
    return None if otras else novedades


def tarea_estandar_clara(evento: EventoSistema) -> str | None:
    """Código de tarea estándar si la evidencia del evento señala una sola falla sin dudas."""
    equipo = evento.equipo
    if not equipo:
        return None
    estandar_del_equipo = {t["codigo"] for t in materiales.tareas_estandar(equipo)}
    if evento.origen == "sensor":
        if "presion_diferencial" in evento.id and "T-F-CAR" in estandar_del_equipo:
            return "T-F-CAR"
        return None
    novedades = _novedades_del_resumen(evento.resumen or "")
    if not novedades:
        return None
    tareas = {TAREA_POR_NOVEDAD[n] for n in novedades if n in TAREA_POR_NOVEDAD}
    if len(tareas) != 1:
        return None
    tarea = next(iter(tareas))
    otras = {n for n in novedades if n not in TAREA_POR_NOVEDAD} - _NOVEDADES_COMPATIBLES.get(tarea, set())
    if otras or tarea not in estandar_del_equipo:
        return None
    return tarea


def orden_inmediata(
    evento: EventoSistema,
    http: PlantaHTTP,
    danio_estructural: bool = False,
    *,
    costo_max_usd: float = 2500,
    almacen: str = "yacimiento",
    siempre_pendiente: frozenset[str] = frozenset(),
    tarea_estandar: bool = False,
) -> OrdenSistema | None:
    """`None` si el evento no tiene equipo (no hay sobre qué abrir una orden). Con
    `tarea_estandar`, si la evidencia es clara se propone la tarea del catálogo (con sus
    repuestos) en vez de una inspección; las reglas deciden igual."""
    if evento.tipo.value != "Escalado":
        raise ValueError(f"orden_inmediata espera un evento Escalado, llegó {evento.tipo!r}")
    if not evento.equipo:
        return None
    codigo_claro = tarea_estandar_clara(evento) if tarea_estandar else None
    tarea = _tarea_del_catalogo(codigo_claro or tarea_de_inspeccion(evento), evento.equipo)
    repuestos = [Repuesto(**r) for r in tarea.get("repuestos", [])] if codigo_claro else []
    detiene = bool(tarea.get("detiene", True))
    borrador = OrdenSistema(
        id=f"ORD-{evento.id}", ts=evento.ts, estado=EstadoOrden.borrador, equipo=evento.equipo,
        evento_ids=[evento.id], tareas=[tarea["codigo"]], detiene_equipo=detiene,
        reduce_inyeccion=False, ejecutor=tarea.get("ejecutor", "propio"), repuestos=repuestos,
    )
    reserva = hay_reserva_disponible(borrador.equipo, http) if detiene else True
    # Anexo: `reduce_inyeccion` según la disponibilidad REAL de la reserva en este momento — si la
    # tarea detiene el equipo y no hay otro del grupo parado para tomar su lugar, se inyecta menos.
    borrador = borrador.model_copy(update={"reduce_inyeccion": detiene and not reserva})
    motivos = evaluar_reglas(
        borrador, materiales, reserva, danio_estructural,
        costo_max_usd=costo_max_usd, almacen=almacen, siempre_pendiente=siempre_pendiente,
    )
    if motivos:
        que = f"tarea {tarea['codigo']} propuesta por evidencia clara" if codigo_claro else "inspección propuesta por el sistema"
        return borrador.model_copy(update={
            "estado": EstadoOrden.pendiente,
            "motivo": f"{que}; " + "; ".join(motivos),
        })
    return borrador.model_copy(update={"estado": EstadoOrden.aprobada})


def detalle_repuestos(orden: OrdenSistema, almacen: str = "yacimiento") -> list[dict]:
    """Anexo C: repuestos con disponibilidad (yacimiento o central) y costo unitario. El `Repuesto`
    del contrato solo admite `codigo` y `cantidad` (`extra="forbid"`), así que este detalle no
    viaja en `sistema/ordenes`: lo muestra la central (`GET /api/ordenes/{id}`) al planificador."""
    detalle = []
    for r in orden.repuestos:
        info = materiales.repuesto(r.codigo) or {}
        stock = info.get("stock", {})
        if stock.get(almacen, 0) >= r.cantidad:
            disponibilidad = almacen
        elif stock.get("central", 0) >= r.cantidad:
            disponibilidad = "central"
        else:
            disponibilidad = "sin stock"
        costo = info.get("costo_usd")
        detalle.append({
            "codigo": r.codigo, "cantidad": r.cantidad, "disponibilidad": disponibilidad,
            "costo_unitario_usd": costo, "costo_total_usd": costo * r.cantidad if costo is not None else None,
        })
    return detalle


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
    orden_borrador = borrador.a_orden_sistema(evento, orden_id).model_copy(
        update={"reduce_inyeccion": borrador.detiene_equipo and not reserva}
    )
    motivos_reglas = evaluar_reglas(
        orden_borrador, materiales, reserva, danio_estructural,
        costo_max_usd=costo_max_usd, almacen=almacen, siempre_pendiente=siempre_pendiente,
    )
    if motivos_reglas:
        return orden_borrador.model_copy(
            update={"estado": EstadoOrden.pendiente, "motivo": "; ".join(motivos_reglas)}
        )
    return orden_borrador.model_copy(update={"estado": EstadoOrden.aprobada})
