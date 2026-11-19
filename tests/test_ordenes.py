from datetime import datetime, timezone

from planta_contrato import EstadoOrden, EventoSistema, Repuesto, TipoEvento

import sistema.procesamiento.ordenes as ordenes_mod
from sistema.llm.analisis import BorradorOrden, CausaProbable, Diagnostico
from sistema.llm.control import ConsumoTokens
from sistema.procesamiento.ordenes import procesar_evento_escalado

AHORA = datetime.now(timezone.utc)


class HttpFalso:
    def __init__(self, bombas=None):
        self._bombas = bombas or []

    def estado(self):
        return {"bombas": self._bombas}


class AnalizadorFalso:
    def __init__(self, respuestas):
        self._respuestas = iter(respuestas)
        self.llamadas = 0

    def analizar(self, evento, consumo=None):
        self.llamadas += 1
        return next(self._respuestas)


def _evento(equipo="B-101") -> EventoSistema:
    return EventoSistema(
        id="EVT-1", ts=AHORA, tipo=TipoEvento.escalado, equipo=equipo, origen="sensor",
        resumen="presión fuera de límite",
    )


def test_borrador_sin_motivos_queda_aprobada(monkeypatch):
    monkeypatch.setattr(ordenes_mod, "buscar_fragmentos", lambda *a, **k: [])
    diagnostico = Diagnostico(
        causas_probables=[],
        borrador=BorradorOrden(equipo="B-101", tareas=["T-I-CAL"], ejecutor="propio"),
    )
    analizador = AnalizadorFalso([diagnostico])

    orden = procesar_evento_escalado(
        _evento(), HttpFalso(), embeddings=None, traductor=None, analizador=analizador,
    )

    assert orden is not None
    assert orden.estado == EstadoOrden.aprobada
    assert analizador.llamadas == 1


def test_borrador_que_no_pasa_reglas_queda_pendiente_con_motivo(monkeypatch):
    monkeypatch.setattr(ordenes_mod, "buscar_fragmentos", lambda *a, **k: [])
    diagnostico = Diagnostico(
        causas_probables=[],
        borrador=BorradorOrden(equipo="B-101", tareas=["T-I-CAL"], ejecutor="contratista"),
    )
    analizador = AnalizadorFalso([diagnostico])

    orden = procesar_evento_escalado(
        _evento(), HttpFalso(), embeddings=None, traductor=None, analizador=analizador,
    )

    assert orden.estado == EstadoOrden.pendiente
    assert "ejecutor" in orden.motivo


def test_f10_no_genera_orden(monkeypatch):
    monkeypatch.setattr(ordenes_mod, "buscar_fragmentos", lambda *a, **k: [])
    diagnostico = Diagnostico(causas_probables=[CausaProbable(descripcion="d", evidencia="e")], borrador=None)
    analizador = AnalizadorFalso([diagnostico])

    orden = procesar_evento_escalado(
        _evento(), HttpFalso(), embeddings=None, traductor=None, analizador=analizador,
    )

    assert orden is None


def test_revision_rechaza_cita_inventada_y_reintenta_una_vez_antes_de_caer_a_inspeccion(monkeypatch):
    monkeypatch.setattr(ordenes_mod, "buscar_fragmentos", lambda *a, **k: [])
    diagnostico_malo = Diagnostico(
        causas_probables=[CausaProbable(descripcion="d", evidencia="e", cita="MBP-4 §9.9")],
        borrador=BorradorOrden(equipo="B-101", tareas=["T-I-CAL"]),
    )
    analizador = AnalizadorFalso([diagnostico_malo, diagnostico_malo])

    orden = procesar_evento_escalado(
        _evento(), HttpFalso(), embeddings=None, traductor=None, analizador=analizador,
    )

    assert analizador.llamadas == 2
    assert orden.estado == EstadoOrden.pendiente
    assert orden.tareas == ["I-VIB"]
    assert "revisión" in orden.motivo


def test_presupuesto_agotado_cae_a_inspeccion_sin_llamar_al_analizador(monkeypatch):
    monkeypatch.setattr(ordenes_mod, "buscar_fragmentos", lambda *a, **k: [])
    analizador = AnalizadorFalso([])
    consumo = ConsumoTokens(pasos=99)

    orden = procesar_evento_escalado(
        _evento(), HttpFalso(), embeddings=None, traductor=None, analizador=analizador, consumo=consumo,
    )

    assert analizador.llamadas == 0
    assert orden.estado == EstadoOrden.pendiente
    assert "presupuesto" in orden.motivo


def test_detiene_equipo_sin_reserva_queda_pendiente(monkeypatch):
    monkeypatch.setattr(ordenes_mod, "buscar_fragmentos", lambda *a, **k: [])
    diagnostico = Diagnostico(
        causas_probables=[],
        borrador=BorradorOrden(equipo="B-101", tareas=["T-B-EMP"], detiene_equipo=True),
    )
    analizador = AnalizadorFalso([diagnostico])
    http = HttpFalso(bombas=[{"tag": "B-101", "estado": "marcha"}, {"tag": "B-102", "estado": "marcha"}])

    orden = procesar_evento_escalado(
        _evento(), http, embeddings=None, traductor=None, analizador=analizador,
    )

    assert orden.estado == EstadoOrden.pendiente
    assert "reserva" in orden.motivo


def test_repuesto_que_excede_costo_queda_pendiente(monkeypatch):
    monkeypatch.setattr(ordenes_mod, "buscar_fragmentos", lambda *a, **k: [])
    diagnostico = Diagnostico(
        causas_probables=[],
        borrador=BorradorOrden(
            equipo="B-101", tareas=["T-B-EMP"],
            repuestos=[Repuesto(codigo="TX-EMP-KIT", cantidad=100)],
        ),
    )
    analizador = AnalizadorFalso([diagnostico])

    orden = procesar_evento_escalado(
        _evento(), HttpFalso(), embeddings=None, traductor=None, analizador=analizador,
    )

    assert orden.estado == EstadoOrden.pendiente
