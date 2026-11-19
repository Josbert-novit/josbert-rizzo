from datetime import datetime, timezone

from planta_contrato import EstadoOrden, OrdenSistema, Repuesto

from sistema.llm.cierre import CierreExtraido
from sistema.procesamiento.cierres import comparar_cierre_con_orden

AHORA = datetime.now(timezone.utc)


def _orden(tareas, repuestos=()) -> OrdenSistema:
    return OrdenSistema(
        id="ORD-1", ts=AHORA, estado=EstadoOrden.aprobada, equipo="B-101", evento_ids=["EVT-1"],
        tareas=tareas, detiene_equipo=False, reduce_inyeccion=False, ejecutor="contratista",
        repuestos=[Repuesto(codigo=c, cantidad=q) for c, q in repuestos],
    )


def test_cierre_igual_a_la_orden_no_da_diferencias():
    orden = _orden(["I-VIB"])
    cierre = CierreExtraido(tareas=["I-VIB"], repuestos=[], horas=3.5)

    assert comparar_cierre_con_orden(cierre, orden) == []


def test_declara_tarea_fuera_de_la_orden():
    orden = _orden(["I-VIB"])
    cierre = CierreExtraido(tareas=["I-VIB", "T-B-ROD"])

    motivos = comparar_cierre_con_orden(cierre, orden)

    assert any("T-B-ROD" in m for m in motivos)


def test_declara_repuesto_fuera_de_la_orden():
    # OT-25-0004: declara TX-SELLO-CAR, que no estaba en la orden.
    orden = _orden(["I-VIB"])
    cierre = CierreExtraido(tareas=["I-VIB"], repuestos=[Repuesto(codigo="TX-SELLO-CAR", cantidad=1)])

    motivos = comparar_cierre_con_orden(cierre, orden)

    assert any("TX-SELLO-CAR" in m for m in motivos)


def test_declara_mas_cantidad_de_un_repuesto_autorizado():
    # OT-25-0026: declara el doble de ACO-ELAST-90 que lo autorizado.
    orden = _orden(["T-B-ALI"], repuestos=[("ACO-ELAST-90", 1)])
    cierre = CierreExtraido(tareas=["T-B-ALI"], repuestos=[Repuesto(codigo="ACO-ELAST-90", cantidad=2)])

    motivos = comparar_cierre_con_orden(cierre, orden)

    assert any("ACO-ELAST-90" in m for m in motivos)


def test_declara_igual_o_menos_cantidad_no_da_diferencia():
    orden = _orden(["T-B-ALI"], repuestos=[("ACO-ELAST-90", 1)])
    cierre = CierreExtraido(tareas=["T-B-ALI"], repuestos=[Repuesto(codigo="ACO-ELAST-90", cantidad=1)])

    assert comparar_cierre_con_orden(cierre, orden) == []
