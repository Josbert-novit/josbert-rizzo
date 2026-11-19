import types
from datetime import datetime, timezone

import sistema.procesamiento.fotos as fotos_mod
from planta_contrato import EstadoOrden, OrdenSistema
from sistema.llm.vision import AnalisisFoto
from sistema.procesamiento.fotos import (
    encolar_o_analizar,
    reabrir_orden_si_danio_estructural,
    reconciliar_fotos_pendientes,
)

AHORA = datetime.now(timezone.utc)


class AlmacenOrdenesFalso:
    def __init__(self):
        self.filas: dict[str, OrdenSistema] = {}

    def conectar(self):
        return types.SimpleNamespace(close=lambda: None)

    def cargar(self, conn, orden_id):
        return self.filas.get(orden_id)

    def guardar(self, conn, orden, pendiente_publicar=True):
        self.filas[orden.id] = orden


class AlmacenFotosFalso:
    def __init__(self):
        self.filas: dict[str, dict] = {}

    def conectar(self):
        return types.SimpleNamespace(close=lambda: None)

    def encolar(self, conn, foto_id, url, orden_id=None):
        self.filas.setdefault(foto_id, {"id": foto_id, "url": url, "orden_id": orden_id, "resuelta": False})

    def pendientes(self, conn):
        return [f for f in self.filas.values() if not f["resuelta"]]

    def marcar_resuelta(self, conn, foto_id):
        self.filas[foto_id]["resuelta"] = True


class ClienteFalso:
    def __init__(self, respuesta: AnalisisFoto):
        self.respuesta = respuesta
        self.llamadas = 0

    def analizar(self, url):
        self.llamadas += 1
        return self.respuesta


def _orden(estado=EstadoOrden.aprobada) -> OrdenSistema:
    return OrdenSistema(
        id="ORD-1", ts=AHORA, estado=estado, equipo="B-101", evento_ids=["EVT-1"],
        tareas=["T-I-CAL"], detiene_equipo=False, reduce_inyeccion=False, ejecutor="propio",
        repuestos=[],
    )


def _patch_almacenes(monkeypatch):
    ordenes = AlmacenOrdenesFalso()
    fotos = AlmacenFotosFalso()
    monkeypatch.setattr(fotos_mod, "persistencia_ordenes", ordenes)
    monkeypatch.setattr(fotos_mod, "persistencia_fotos", fotos)
    return ordenes, fotos


def test_reabrir_sin_danio_no_hace_nada(monkeypatch):
    ordenes, _ = _patch_almacenes(monkeypatch)
    ordenes.filas["ORD-1"] = _orden()

    reabrio = reabrir_orden_si_danio_estructural(None, "ORD-1", AnalisisFoto(danio_estructural=False, descripcion="ok"))

    assert reabrio is False
    assert ordenes.filas["ORD-1"].estado == EstadoOrden.aprobada


def test_reabrir_con_danio_pasa_a_pendiente_con_motivo(monkeypatch):
    ordenes, _ = _patch_almacenes(monkeypatch)
    ordenes.filas["ORD-1"] = _orden(estado=EstadoOrden.aprobada)

    reabrio = reabrir_orden_si_danio_estructural(
        None, "ORD-1", AnalisisFoto(danio_estructural=True, descripcion="grieta en la carcasa"),
    )

    assert reabrio is True
    assert ordenes.filas["ORD-1"].estado == EstadoOrden.pendiente
    assert "grieta en la carcasa" in ordenes.filas["ORD-1"].motivo


def test_reabrir_no_revierte_un_cierre_ya_hecho(monkeypatch):
    ordenes, _ = _patch_almacenes(monkeypatch)
    ordenes.filas["ORD-1"] = _orden(estado=EstadoOrden.cerrada)

    reabrio = reabrir_orden_si_danio_estructural(
        None, "ORD-1", AnalisisFoto(danio_estructural=True, descripcion="grieta"),
    )

    assert reabrio is False
    assert ordenes.filas["ORD-1"].estado == EstadoOrden.cerrada


def test_reabrir_orden_inexistente_no_falla(monkeypatch):
    _patch_almacenes(monkeypatch)

    assert reabrir_orden_si_danio_estructural(None, "ORD-NO-EXISTE", AnalisisFoto(danio_estructural=True, descripcion="x")) is False


def test_encolar_o_analizar_sin_enlace_encola_y_no_llama_al_cliente(monkeypatch):
    _, fotos = _patch_almacenes(monkeypatch)
    monkeypatch.setattr(fotos_mod, "enlace_arriba", lambda: False)
    cliente = ClienteFalso(AnalisisFoto(danio_estructural=True, descripcion="x"))

    resultado = encolar_o_analizar("FOTO-1", "http://x/1.jpg", orden_id="ORD-1", cliente=cliente)

    assert resultado is None
    assert cliente.llamadas == 0
    assert fotos.filas["FOTO-1"]["resuelta"] is False


def test_encolar_o_analizar_con_enlace_resuelve_y_reabre_la_orden(monkeypatch):
    ordenes, fotos = _patch_almacenes(monkeypatch)
    monkeypatch.setattr(fotos_mod, "enlace_arriba", lambda: True)
    ordenes.filas["ORD-1"] = _orden(estado=EstadoOrden.aprobada)
    cliente = ClienteFalso(AnalisisFoto(danio_estructural=True, descripcion="grieta"))

    resultado = encolar_o_analizar("FOTO-1", "http://x/1.jpg", orden_id="ORD-1", cliente=cliente)

    assert resultado.danio_estructural is True
    assert fotos.filas["FOTO-1"]["resuelta"] is True
    assert ordenes.filas["ORD-1"].estado == EstadoOrden.pendiente


def test_reconciliar_sin_enlace_no_procesa_nada(monkeypatch):
    _, fotos = _patch_almacenes(monkeypatch)
    monkeypatch.setattr(fotos_mod, "enlace_arriba", lambda: False)
    fotos.filas["FOTO-1"] = {"id": "FOTO-1", "url": "http://x/1.jpg", "orden_id": None, "resuelta": False}
    cliente = ClienteFalso(AnalisisFoto(danio_estructural=False, descripcion="x"))

    resueltas = reconciliar_fotos_pendientes(cliente=cliente)

    assert resueltas == 0
    assert cliente.llamadas == 0


def test_reconciliar_con_enlace_resuelve_las_encoladas(monkeypatch):
    ordenes, fotos = _patch_almacenes(monkeypatch)
    monkeypatch.setattr(fotos_mod, "enlace_arriba", lambda: True)
    ordenes.filas["ORD-1"] = _orden(estado=EstadoOrden.aprobada)
    fotos.filas["FOTO-1"] = {"id": "FOTO-1", "url": "http://x/1.jpg", "orden_id": "ORD-1", "resuelta": False}
    cliente = ClienteFalso(AnalisisFoto(danio_estructural=True, descripcion="grieta"))

    resueltas = reconciliar_fotos_pendientes(cliente=cliente)

    assert resueltas == 1
    assert fotos.filas["FOTO-1"]["resuelta"] is True
    assert ordenes.filas["ORD-1"].estado == EstadoOrden.pendiente


def test_sin_clave_de_vision_la_foto_no_queda_pendiente_para_siempre(monkeypatch):
    """Lote 5b (E2): sin NVIDIA_API_KEY, al volver el enlace la foto se resuelve sin análisis."""
    _, fotos = _patch_almacenes(monkeypatch)
    monkeypatch.setattr(fotos_mod.config, "nvidia_api_key", "")
    monkeypatch.setattr(fotos_mod, "enlace_arriba", lambda: False)
    encolar_o_analizar("FOTO-1", "http://x/1.jpg")
    assert fotos.filas["FOTO-1"]["resuelta"] is False  # sin enlace: pendiente (el latido la muestra)

    monkeypatch.setattr(fotos_mod, "enlace_arriba", lambda: True)
    assert reconciliar_fotos_pendientes() == 1
    assert fotos.filas["FOTO-1"]["resuelta"] is True
