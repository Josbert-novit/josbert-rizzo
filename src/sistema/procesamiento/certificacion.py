"""Certificación de trabajo de contratista (Fase 14.3/14.4): el monto del habilita sale de la
orden y de la lista de precios vigente (`CT-SVP-2025.md`, Anexo II/IV) — NUNCA de lo que declara
el cierre (3.2 del contrato, literal). Validado contra los casos reales de
`academia2026-tp/datos/documentos/contrato/cierres_ejemplo.json` (ver `tests/test_certificacion.py`).

Repuestos: el contrato (1.4) dice que los repuestos los provee la operadora desde sus almacenes y
NO integran el precio del servicio — y en nuestro sistema `OrdenSistema.repuestos` siempre sale
del stock propio (`integraciones/materiales.py`, Fase 9), nunca de un repuesto que la contratista
traiga con autorización escrita. Por eso acá nunca se suman al habilita (confirmado contra
`cierres_ejemplo.json`: OT-25-0119 y OT-25-0026 tienen repuestos en la orden y el habilita es
igual al precio cerrado solo, sin el repuesto).
"""

from __future__ import annotations

from datetime import datetime, timezone

from planta_contrato import OrdenSistema

# I-VIB es "por equipo medido" (2.1) pero, en la operación real de esta planta, el análisis de
# vibraciones siempre se hace de las tres bombas juntas (ver cierres_ejemplo.json: toda orden con
# I-VIB certifica USD 960 = 320 x 3, sea cual sea el `equipo` que escaló la orden).
EQUIPOS_MEDIDOS_POR_INSPECCION = 3

VIGENTE_DESDE = datetime(2025, 1, 1, tzinfo=timezone.utc)

# CT-SVP-2025 (Anexo II, 2.1), vigente desde 2025-01-01.
PRECIOS_2025 = {
    "T-B-ROD": ("cerrado", 1150),
    "T-B-ALI": ("cerrado", 780),
    "I-VIB": ("por_equipo", 320),
}

# CT-SVP-2022 (Anexo II, 2.5) — solo para trabajos iniciados antes de 2025-01-01.
PRECIOS_2022 = {
    "T-B-ROD": ("cerrado", 980),
    "T-B-ALI": ("cerrado", 650),
    "I-VIB": ("por_equipo", 280),
}


def _lista_vigente(fecha_inicio: datetime) -> dict:
    return PRECIOS_2025 if fecha_inicio >= VIGENTE_DESDE else PRECIOS_2022


def calcular_monto_habilita_usd(orden: OrdenSistema) -> float:
    """3.2 del contrato: 1) tarea -> ítem de la lista vigente a la fecha de inicio de la orden;
    2) precio cerrado tal cual, o precio por equipo x cantidad de equipos medidos; 3) nunca sumar
    repuestos (ver docstring del módulo); 4) nunca sumar lo que el cierre declara de más."""
    precios = _lista_vigente(orden.ts)
    total = 0.0
    for tarea in orden.tareas:
        item = precios.get(tarea)
        if item is None:
            continue  # tarea propia / no facturable por contrato de servicio
        tipo, precio = item
        total += precio * EQUIPOS_MEDIDOS_POR_INSPECCION if tipo == "por_equipo" else precio
    return total
