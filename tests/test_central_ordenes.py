import types
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from planta_contrato import EstadoOrden, OrdenSistema

import sistema.interfaces.central as central
from sistema.interfaces.central import CertificacionBody, ObservacionBody, RechazoBody

AHORA = datetime.now(timezone.utc)


class AlmacenFalso:
    """Reemplaza `persistencia.ordenes` por un diccionario en memoria — los endpoints de
    `central.py` no necesitan saber si hay un Postgres real detrás, solo llaman a
    `conectar`/`cargar`/`guardar`/`cargar_todas`."""

    def __init__(self):
        self.filas: dict[str, OrdenSistema] = {}

    def conectar(self):
        return types.SimpleNamespace(close=lambda: None)

    def cargar(self, conn, orden_id):
        return self.filas.get(orden_id)

    def cargar_todas(self, conn):
        return list(self.filas.values())

    def guardar(self, conn, orden, pendiente_publicar=True):
        self.filas[orden.id] = orden


@pytest.fixture
def almacen(monkeypatch):
    falso = AlmacenFalso()
    monkeypatch.setattr(central.persistencia_ordenes, "conectar", falso.conectar)
    monkeypatch.setattr(central.persistencia_ordenes, "cargar", falso.cargar)
    monkeypatch.setattr(central.persistencia_ordenes, "cargar_todas", falso.cargar_todas)
    monkeypatch.setattr(central.persistencia_ordenes, "guardar", falso.guardar)
    return falso


def _orden(estado=EstadoOrden.pendiente, **kw) -> OrdenSistema:
    base = dict(
        id="ORD-1", ts=AHORA, estado=estado, equipo="B-101", evento_ids=["EVT-1"],
        tareas=["T-I-CAL"], detiene_equipo=False, reduce_inyeccion=False, ejecutor="propio",
        repuestos=[],
    )
    base.update(kw)
    return OrdenSistema(**base)


def test_listar_ordenes_pendientes_filtra_por_estado(almacen):
    almacen.filas["ORD-1"] = _orden(estado=EstadoOrden.pendiente)
    almacen.filas["ORD-2"] = _orden(id="ORD-2", estado=EstadoOrden.aprobada)

    pendientes = central.listar_ordenes_pendientes()

    assert [o["id"] for o in pendientes] == ["ORD-1"]


def test_aprobar_orden_pendiente_la_aprueba(almacen):
    almacen.filas["ORD-1"] = _orden(estado=EstadoOrden.pendiente, motivo="costo")

    resultado = central.aprobar_orden("ORD-1")

    assert resultado["estado"] == "Aprobada"
    assert resultado["motivo"] is None
    assert almacen.filas["ORD-1"].estado == EstadoOrden.aprobada


def test_aprobar_orden_que_no_esta_pendiente_falla(almacen):
    almacen.filas["ORD-1"] = _orden(estado=EstadoOrden.aprobada)

    with pytest.raises(HTTPException) as exc_info:
        central.aprobar_orden("ORD-1")

    assert exc_info.value.status_code == 409


def test_rechazar_orden_exige_motivo_y_lo_guarda(almacen):
    almacen.filas["ORD-1"] = _orden(estado=EstadoOrden.pendiente)

    resultado = central.rechazar_orden("ORD-1", RechazoBody(motivo="sin stock"))

    assert resultado["estado"] == "Rechazada"
    assert resultado["motivo"] == "sin stock"


def test_cerrar_orden_ejecutada(almacen):
    almacen.filas["ORD-1"] = _orden(estado=EstadoOrden.ejecutada)

    resultado = central.cerrar_orden("ORD-1")

    assert resultado["estado"] == "Cerrada"


def test_certificar_orden_ejecutada_calcula_el_monto(almacen):
    almacen.filas["ORD-1"] = _orden(estado=EstadoOrden.ejecutada, ejecutor="contratista", tareas=["I-VIB"])

    resultado = central.certificar_orden("ORD-1", CertificacionBody(habilita="HAB-2026-0001"))

    assert resultado["estado"] == "Certificada"
    assert resultado["habilita"] == "HAB-2026-0001"
    assert resultado["monto_habilita_usd"] == 960


def test_certificar_orden_de_trabajo_propio_falla(almacen):
    almacen.filas["ORD-1"] = _orden(estado=EstadoOrden.ejecutada, ejecutor="propio")

    with pytest.raises(HTTPException) as exc_info:
        central.certificar_orden("ORD-1", CertificacionBody(habilita="HAB-2026-0001"))

    assert exc_info.value.status_code == 409


def test_observar_orden_ejecutada_con_motivo(almacen):
    almacen.filas["ORD-1"] = _orden(estado=EstadoOrden.ejecutada, ejecutor="contratista")

    resultado = central.observar_orden("ORD-1", ObservacionBody(motivo="movilización mal cobrada"))

    assert resultado["estado"] == "Observada"
    assert resultado["motivo"] == "movilización mal cobrada"


def test_cerrar_orden_de_contratista_falla(almacen):
    almacen.filas["ORD-1"] = _orden(estado=EstadoOrden.ejecutada, ejecutor="contratista")

    with pytest.raises(HTTPException) as exc_info:
        central.cerrar_orden("ORD-1")

    assert exc_info.value.status_code == 409


def test_orden_inexistente_da_404(almacen):
    with pytest.raises(HTTPException) as exc_info:
        central.aprobar_orden("ORD-NO-EXISTE")

    assert exc_info.value.status_code == 404
