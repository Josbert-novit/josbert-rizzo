from datetime import datetime, timezone

from planta_contrato import EstadoOrden, OrdenSistema, Repuesto

AHORA = datetime.now(timezone.utc)


def _orden() -> OrdenSistema:
    return OrdenSistema(
        id="ORD-1", ts=AHORA, estado=EstadoOrden.pendiente, equipo="B-101",
        evento_ids=["EVT-1"], tareas=["T-B-VAL"], detiene_equipo=True, reduce_inyeccion=False,
        ejecutor="propio", repuestos=[Repuesto(codigo="TX-VAL-KIT", cantidad=1)],
        motivo="reserva no disponible",
    )


def test_roundtrip_conserva_todos_los_campos():
    orden = _orden()

    recuperada = OrdenSistema.model_validate_json(orden.model_dump_json())

    assert recuperada.id == orden.id
    assert recuperada.estado == EstadoOrden.pendiente
    assert recuperada.repuestos == [Repuesto(codigo="TX-VAL-KIT", cantidad=1)]
    assert recuperada.motivo == "reserva no disponible"


def test_model_copy_para_avanzar_estado_no_pierde_los_demas_campos():
    orden = _orden()

    aprobada = orden.model_copy(update={"estado": EstadoOrden.aprobada, "motivo": None})

    assert aprobada.id == orden.id
    assert aprobada.estado == EstadoOrden.aprobada
    assert aprobada.motivo is None
    assert aprobada.repuestos == orden.repuestos
