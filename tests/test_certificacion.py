"""Casos reales de `academia2026-tp/datos/documentos/contrato/cierres_ejemplo.json` (Fase 14.4):
`monto_habilita_usd` sale siempre de la orden + la lista de precios, nunca de lo declarado."""

from datetime import datetime, timezone

import pytest
from planta_contrato import EstadoOrden, OrdenSistema, Repuesto

from sistema.procesamiento.certificacion import calcular_monto_habilita_usd


def _orden(fecha_inicio: str, equipo: str, tareas: list[str], repuestos=()) -> OrdenSistema:
    return OrdenSistema(
        id="ORD-1", ts=datetime.fromisoformat(fecha_inicio), estado=EstadoOrden.aprobada,
        equipo=equipo, evento_ids=["EVT-1"], tareas=tareas, detiene_equipo=False,
        reduce_inyeccion=False, ejecutor="contratista",
        repuestos=[Repuesto(codigo=c, cantidad=q) for c, q in repuestos],
    )


@pytest.mark.parametrize(
    "fecha_inicio,equipo,tareas,repuestos,esperado",
    [
        # OT-24-0010: I-VIB en B-101, 2024-11-05 -> CT-SVP-2022 (280 x 3)
        ("2024-11-05T04:25-03:00", "B-101", ["I-VIB"], [], 840),
        # OT-24-0015: T-B-ROD en B-101, 2024-11-21 -> CT-SVP-2022 (980)
        ("2024-11-21T16:30-03:00", "B-101", ["T-B-ROD"], [], 980),
        # OT-25-0004: I-VIB en PLANTA, 2025-01-12 -> CT-SVP-2025 (320 x 3)
        ("2025-01-12T17:30-03:00", "PLANTA", ["I-VIB"], [], 960),
        # OT-25-0026: T-B-ALI en B-101 con repuesto en la orden -> 780 (el repuesto NO se suma)
        ("2025-03-11T19:35-03:00", "B-101", ["T-B-ALI"], [("ACO-ELAST-90", 1)], 780),
        # OT-25-0036/46/79/110, OT-26-0021/32/61: I-VIB en PLANTA -> 960
        ("2025-04-14T08:55-03:00", "PLANTA", ["I-VIB"], [], 960),
        ("2025-11-12T13:15-03:00", "PLANTA", ["I-VIB"], [], 960),
        # OT-25-0119: T-B-ALI en B-101 con repuesto en la orden -> 780 (otra vez, sin sumar)
        ("2025-12-03T01:45-03:00", "B-101", ["T-B-ALI"], [("ACO-ELAST-90", 1)], 780),
    ],
)
def test_monto_habilita_contra_casos_reales(fecha_inicio, equipo, tareas, repuestos, esperado):
    orden = _orden(fecha_inicio, equipo, tareas, repuestos)

    assert calcular_monto_habilita_usd(orden) == esperado


def test_tarea_propia_no_facturable_no_suma_nada():
    orden = _orden("2025-06-01T10:00-03:00", "B-101", ["T-B-EMP"])

    assert calcular_monto_habilita_usd(orden) == 0
