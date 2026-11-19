from datetime import datetime, timezone

from planta_contrato import EventoSistema, TipoEvento

from sistema.llm.analisis import (
    Analizador,
    BorradorOrden,
    Diagnostico,
    _hint_causa_repetida,
    detectar_codigo_falla,
)
from sistema.rag.fallas import TraductorFallas

AHORA = datetime.now(timezone.utc)


class HttpFalso:
    def __init__(self, historial_ordenes=None, historial=None):
        self._historial_ordenes = historial_ordenes or []
        self._historial = historial or []

    def historial(self, tag, variable, desde, hasta):
        return self._historial

    def historial_ordenes(self, equipo=None, desde=None, hasta=None):
        return [o for o in self._historial_ordenes if equipo is None or o.get("equipo") == equipo]

    def secciones(self, doc_id, numero=None):
        return []


class ChatFalso:
    def __init__(self, respuesta: Diagnostico) -> None:
        self.respuesta = respuesta
        self.llamadas: list = []

    def invoke(self, mensajes, config=None):
        self.llamadas.append(mensajes)
        return self.respuesta


def _buscar_citas_vacio(pregunta, k=5, equipo=None):
    return []


def _evento(resumen="valor fuera de límite", equipo="B-101") -> EventoSistema:
    return EventoSistema(
        id="EVT-1", ts=AHORA, tipo=TipoEvento.escalado, equipo=equipo, origen="sensor",
        resumen=resumen,
    )


def _diagnostico_con_borrador(equipo="B-101") -> Diagnostico:
    return Diagnostico(
        causas_probables=[], borrador=BorradorOrden(equipo=equipo, tareas=["T-I-CAL"]),
    )


def test_f10_nunca_genera_orden_y_no_llama_al_llm():
    traductor = TraductorFallas(HttpFalso())
    chat = ChatFalso(_diagnostico_con_borrador())
    analizador = Analizador(HttpFalso(), traductor, _buscar_citas_vacio, chat=chat)
    evento = _evento(resumen="sensor F10 con deriva en B-101")

    diagnostico = analizador.analizar(evento)

    assert diagnostico.borrador is None
    assert chat.llamadas == []


def test_evento_normal_delega_al_chat():
    traductor = TraductorFallas(HttpFalso())
    chat = ChatFalso(_diagnostico_con_borrador())
    analizador = Analizador(HttpFalso(), traductor, _buscar_citas_vacio, chat=chat)
    evento = _evento()

    diagnostico = analizador.analizar(evento)

    assert diagnostico.borrador is not None
    assert len(chat.llamadas) == 1


def test_detectar_codigo_falla_directo():
    traductor = TraductorFallas(HttpFalso())
    assert detectar_codigo_falla("se ve F09 en la bomba", traductor) == "F09"


def test_detectar_codigo_falla_via_fabricante():
    tabla = "Código | Falla | Fabricante | Tarea | Ejecutor\nF09 | Cavitación | TX-T07 | I-VIB | propio"
    traductor = TraductorFallas(HttpFalso())
    traductor._http = type("H", (), {"secciones": lambda self, doc_id, numero=None: [{"texto": tabla}]})()

    assert detectar_codigo_falla("reporta código TX-T07 en la bomba", traductor) == "F09"


def test_detectar_codigo_falla_sin_coincidencia():
    traductor = TraductorFallas(HttpFalso())
    assert detectar_codigo_falla("presión alta sostenida", traductor) is None


def test_hint_causa_repetida_valvulas_en_b101():
    previas = [
        {"equipo": "B-101", "tareas": ["T-B-VAL"]},
        {"equipo": "B-101", "tareas": ["T-B-VAL"]},
    ]
    hint = _hint_causa_repetida("B-101", previas)
    assert hint is not None and "acople" in hint


def test_hint_causa_repetida_valvulas_en_mas_de_una_bomba_apunta_a_f201b():
    previas = [
        {"equipo": "B-101", "tareas": ["T-B-VAL"]},
        {"equipo": "B-102", "tareas": ["T-B-VAL"]},
    ]
    hint = _hint_causa_repetida("B-102", previas)
    assert hint is not None and "F-201B" in hint


def test_hint_causa_repetida_sin_repeticion_no_da_hint():
    previas = [{"equipo": "B-101", "tareas": ["T-B-VAL"]}]
    assert _hint_causa_repetida("B-101", previas) is None


def test_hint_aceite_en_b102():
    previas = [
        {"equipo": "B-102", "tareas": ["T-B-ACE"]},
        {"equipo": "B-102", "tareas": ["T-B-ACE"]},
    ]
    hint = _hint_causa_repetida("B-102", previas)
    assert hint is not None and "retén" in hint


def test_borrador_a_orden_sistema_usa_el_evento_id():
    borrador = BorradorOrden(equipo="B-101", tareas=["T-I-CAL"])
    evento = _evento()

    orden = borrador.a_orden_sistema(evento, "ORD-1")

    assert orden.evento_ids == [evento.id]
    assert orden.equipo == "B-101"
    assert orden.estado.value == "Borrador"
