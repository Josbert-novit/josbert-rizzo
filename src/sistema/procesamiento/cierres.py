"""Comparación cierre vs. orden (Fase 14.2): lo que el técnico declara haber hecho
(`llm/cierre.py`) contra lo que la orden pedía — igual principio que `procesamiento/revisor.py`
(Fase 11) pero para el otro extremo del flujo: acá no se verifica el contenido que propuso un LLM
propio, se verifica lo que declara un tercero (la contratista) contra la fuente de verdad (la
orden). El contrato (3.3) es explícito: "si el cierre declara algo distinto de la orden, la orden
pasa a Observada" — estas diferencias son la base de esa decisión (que toma un humano en
`interfaces/central.py`, no este módulo).
"""

from __future__ import annotations

from planta_contrato import OrdenSistema

from sistema.llm.cierre import CierreExtraido


def comparar_cierre_con_orden(cierre: CierreExtraido, orden: OrdenSistema) -> list[str]:
    motivos: list[str] = []

    tareas_pedidas = set(orden.tareas)
    for tarea in cierre.tareas:
        if tarea not in tareas_pedidas:
            motivos.append(f"declara la tarea '{tarea}', que no estaba en la orden")

    repuestos_pedidos = {r.codigo: r.cantidad for r in orden.repuestos}
    for repuesto in cierre.repuestos:
        cantidad_pedida = repuestos_pedidos.get(repuesto.codigo)
        if cantidad_pedida is None:
            motivos.append(f"declara el repuesto '{repuesto.codigo}', que no estaba en la orden")
        elif repuesto.cantidad > cantidad_pedida:
            motivos.append(
                f"declara {repuesto.cantidad} de '{repuesto.codigo}', la orden autorizaba {cantidad_pedida}"
            )

    return motivos
