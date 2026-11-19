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

from sistema.integraciones import materiales
from sistema.llm.cierre import CierreExtraido

# Horas incluidas en la orden = horas del catálogo de cada tarea (I-VIB por equipo medido, x3 en
# esta planta, igual que `certificacion.EQUIPOS_MEDIDOS_POR_INSPECCION`). Se marca la diferencia si
# lo declarado supera las incluidas en más de este margen — calibrado contra
# `cierres_ejemplo.json`: OT-24-0015 declara 26,5 h sobre 16 h de T-B-ROD (se marca); 5,5 h sobre
# 5 h de T-B-ALI o 3,5 h de I-VIB x3 no se marcan.
MARGEN_HORAS = 1.25
_POR_EQUIPO = {"I-VIB": 3}


def horas_incluidas(tareas: list[str]) -> float | None:
    catalogo = {t["codigo"]: t for t in materiales.tareas_estandar() + materiales.tareas_no_estandar()}
    if not tareas or any(t not in catalogo for t in tareas):
        return None
    return sum(float(catalogo[t].get("horas", 0)) * _POR_EQUIPO.get(t, 1) for t in tareas)


def _tareas_que_detienen(tareas: list[str]) -> bool:
    catalogo = {t["codigo"]: t for t in materiales.tareas_estandar() + materiales.tareas_no_estandar()}
    return any(catalogo.get(t, {}).get("detiene", False) for t in tareas)


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

    tareas_hechas = set(cierre.tareas)
    for tarea in orden.tareas:
        if tarea not in tareas_hechas:
            motivos.append(f"la orden pedía la tarea '{tarea}' y el cierre no la declara")

    usados = {r.codigo: r.cantidad for r in cierre.repuestos}
    for codigo, cantidad in repuestos_pedidos.items():
        usado = usados.get(codigo, 0)
        if usado < cantidad:
            motivos.append(f"se reservaron {cantidad} de '{codigo}' y el cierre declara {usado} usados")

    incluidas = horas_incluidas(orden.tareas)
    if cierre.horas is not None and incluidas and cierre.horas > incluidas * MARGEN_HORAS:
        motivos.append(
            f"declara {cierre.horas:g} h; la orden incluye {incluidas:g} h — horas extra no autorizadas"
        )

    if not cierre.resultado_prueba:
        if _tareas_que_detienen(orden.tareas):
            motivos.append("no informa el resultado de la prueba final")
    elif _prueba_con_falla(cierre.resultado_prueba):
        motivos.append(f"la prueba final no salió bien: '{cierre.resultado_prueba}'")

    return motivos


def _prueba_con_falla(resultado: str) -> bool:
    r = resultado.lower()
    if "sin pérdida" in r or "sin perdida" in r:
        return False
    return any(p in r for p in ("pérdida", "perdida", "pierde", "gotea", "no levant", "no llega"))
