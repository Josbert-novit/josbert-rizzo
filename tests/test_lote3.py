"""Lote 3 de la Fase 15: orden inmediata sin LLM (H10/H12), anclaje de valores a la
transcripción (H11) y salida de los callejones sin salida del diálogo (H13)."""

from datetime import datetime, timezone

import pytest
from planta_contrato import EstadoOrden, EventoSistema, OrdenSistema, TipoEvento

from sistema.dominio.parte import FilaParteInterna, Parte
from sistema.procesamiento.dialogo import _siguiente_fila_pendiente
from sistema.procesamiento.interpretacion import (
    _heredar_equipo_si_es_componente,
    _preguntar_equipo_si_tiene_valor,
    anclar_valor,
    menciona_componente,
)
from sistema.procesamiento.ordenes import (
    orden_abierta_equivalente,
    orden_inmediata,
    sumar_evento,
    tarea_de_inspeccion,
)
from sistema.procesamiento.resolucion import Resolucion, ResolverNombres, ResultadoResolucion

AHORA = datetime.now(timezone.utc)


def _evento(id_="EVT-SENSOR-B-101-vibracion-2026-11-10T04:42:30-03:00", equipo="B-101", resumen="vibración sostenida"):
    return EventoSistema(id=id_, ts=AHORA, tipo=TipoEvento.escalado, equipo=equipo, origen="sensor", resumen=resumen)


class HttpEstado:
    def __init__(self, bombas):
        self._bombas = bombas

    def estado(self):
        return {"bombas": [{"tag": t, "estado": e} for t, e in self._bombas.items()]}


TODAS_EN_MARCHA = HttpEstado({"B-101": "marcha", "B-102": "marcha", "B-103": "marcha"})
CON_RESERVA = HttpEstado({"B-101": "marcha", "B-102": "marcha", "B-103": "parada"})


# --- orden inmediata ----------------------------------------------------------------------

def test_tarea_de_inspeccion_por_tipo_de_problema():
    assert tarea_de_inspeccion(_evento()) == "I-VIB"
    assert tarea_de_inspeccion(_evento(id_="EVT-SENSOR-B-101-temperatura_aceite-x", resumen="temperatura alta")) == "I-VIB"
    assert tarea_de_inspeccion(_evento(id_="EVT-SENSOR-B-102-presion_descarga-x", resumen="presión alta")) == "I-VAL"
    assert tarea_de_inspeccion(_evento(id_="EVT-SENSOR-F-201B-presion_diferencial-x", equipo="F-201B",
                                       resumen="diferencial alto")) == "I-VAL"


@pytest.mark.parametrize("http", [TODAS_EN_MARCHA, CON_RESERVA])
def test_orden_inmediata_de_inspeccion_queda_pendiente_con_motivo(http):
    """I-VIB/I-VAL son tareas NO estándar en el catálogo: por reglas nunca se aprueban solas (D1)."""
    orden = orden_inmediata(_evento(), http)
    assert orden.id == "ORD-EVT-SENSOR-B-101-vibracion-2026-11-10T04:42:30-03:00"
    assert orden.estado == EstadoOrden.pendiente
    assert orden.equipo == "B-101"
    assert orden.tareas == ["I-VIB"]
    assert orden.repuestos == []
    assert "no está en el catálogo de tareas estándar" in orden.motivo


def test_orden_inmediata_toma_detiene_y_ejecutor_del_catalogo():
    vib = orden_inmediata(_evento(), TODAS_EN_MARCHA)
    assert (vib.detiene_equipo, vib.ejecutor) == (False, "contratista")  # I-VIB en materiales.yaml
    val = orden_inmediata(_evento(id_="EVT-SENSOR-B-102-presion_descarga-x", equipo="B-102", resumen="presión"),
                          TODAS_EN_MARCHA)
    assert (val.detiene_equipo, val.ejecutor) == (True, "propio")  # I-VAL
    assert "no hay equipo de reserva" in val.motivo


def test_orden_inmediata_sin_equipo_no_genera_orden():
    assert orden_inmediata(_evento(equipo=None), TODAS_EN_MARCHA) is None


def test_orden_inmediata_rechaza_eventos_que_no_son_escalado():
    evento = EventoSistema(id="E", ts=AHORA, tipo=TipoEvento.sin_novedad, equipo="B-101", origen="sensor", resumen="ok")
    with pytest.raises(ValueError):
        orden_inmediata(evento, TODAS_EN_MARCHA)


def test_no_duplica_orden_abierta_del_mismo_equipo_y_tareas():
    primera = orden_inmediata(_evento(), TODAS_EN_MARCHA)
    segunda = orden_inmediata(_evento(id_="EVT-SENSOR-B-101-vibracion-OTRA-HORA"), TODAS_EN_MARCHA)
    existente = orden_abierta_equivalente(segunda, [primera])
    assert existente is primera
    sumada = sumar_evento(existente, "EVT-SENSOR-B-101-vibracion-OTRA-HORA")
    assert sumada.id == primera.id
    assert sumada.evento_ids == [*primera.evento_ids, "EVT-SENSOR-B-101-vibracion-OTRA-HORA"]
    assert sumada.estado == primera.estado  # la decisión ya tomada no cambia


def test_sumar_evento_ya_incluido_no_cambia_nada():
    orden = orden_inmediata(_evento(), TODAS_EN_MARCHA)
    assert sumar_evento(orden, orden.evento_ids[0]) is orden


def test_orden_cerrada_no_cuenta_como_equivalente():
    primera = orden_inmediata(_evento(), TODAS_EN_MARCHA).model_copy(update={"estado": EstadoOrden.cerrada})
    segunda = orden_inmediata(_evento(id_="EVT-OTRO"), TODAS_EN_MARCHA)
    assert orden_abierta_equivalente(segunda, [primera]) is None


def test_otro_equipo_u_otra_tarea_no_es_equivalente():
    b101 = orden_inmediata(_evento(), TODAS_EN_MARCHA)
    b102 = orden_inmediata(_evento(id_="EVT-B102-vibracion", equipo="B-102"), TODAS_EN_MARCHA)
    val = orden_inmediata(_evento(id_="EVT-B101-presion", resumen="presión"), CON_RESERVA)
    assert orden_abierta_equivalente(b102, [b101]) is None
    assert orden_abierta_equivalente(val, [b101]) is None


# --- anclar valores a la transcripción -----------------------------------------------------

@pytest.mark.parametrize(
    "valor, texto, esperado",
    [
        (1063, "Bueno, la del medio está en 1630 de descarga.", 1630),  # caso real
        (0.5, "El pulmón está en 5 metros.", 5),  # caso real
        (1630, "la del medio está en 1630", 1630),  # ya estaba bien
        (1.2, "El filtro B medió uno con dos de diferencial. La dos está en 1590 PSI", 1.2),  # en palabras
        (113, "la 1 está en 113 de descarga", 113),
        (31, "la uno 113 y la dos 131", 31),  # dos candidatos posibles... ninguno con mismos dígitos
        (66, "113 de descarga y 66 grados", 66),
        (None, "113", None),
        (7, "sin números", 7),
    ],
)
def test_anclar_valor(valor, texto, esperado):
    assert anclar_valor(valor, texto) == esperado


def test_anclar_valor_ambiguo_no_toca():
    """Dos números con los mismos dígitos en el texto: no se adivina."""
    assert anclar_valor(1063, "1630 de descarga y 1360 de succión") == 1063


# --- callejones sin salida del diálogo (escenario ataque) ----------------------------------

@pytest.mark.parametrize("dicho", ["empaquetadura", "foto de la empaquetadura", "pérdida por la empaquetadura",
                                   "LAN paqueta dura", "el rodamiento"])
def test_menciona_componente(dicho):
    assert menciona_componente(dicho)


def test_pieza_sin_fila_anterior_toma_el_unico_equipo_de_la_nota():
    r = _heredar_equipo_si_es_componente(
        "empaquetadura", Resolucion(ResultadoResolucion.no_encontrado), None, ["B-102"])
    assert (r.resultado, r.tag) == (ResultadoResolucion.resuelto, "B-102")


def test_pieza_sin_fila_anterior_con_varios_equipos_en_la_nota_no_adivina():
    no = Resolucion(ResultadoResolucion.no_encontrado)
    assert _heredar_equipo_si_es_componente("empaquetadura", no, None, ["B-102", "F-201A"]) is no


def test_fila_sin_equipo_con_valor_pregunta_con_opciones():
    r = _preguntar_equipo_si_tiene_valor(Resolucion(ResultadoResolucion.no_encontrado), 10.0, ["B-101", "F-201B"])
    assert r.resultado == ResultadoResolucion.ambiguo
    assert r.candidatos == ["B-101", "F-201B"]


def test_fila_sin_equipo_sin_valor_no_pregunta():
    no = Resolucion(ResultadoResolucion.no_encontrado)
    assert _preguntar_equipo_si_tiene_valor(no, None, ["B-101"]) is no


def _fila(tag=None, valor=None, candidatos=None, requiere=False):
    return FilaParteInterna(
        tag_dicho="x", tag=tag, ambiguo=bool(candidatos), candidatos=candidatos or [], variable="v",
        valor=valor, unidad=None, observacion=None, hora=AHORA, fotos=[], requiere_confirmacion=requiere,
        motivo_confirmacion=None,
    )


def test_dialogo_saltea_filas_sin_equipo_ni_valor():
    parte = Parte(id="P", filas=[_fila(), _fila(tag="B-101", valor=1.0, requiere=True)])
    assert _siguiente_fila_pendiente(parte) == 1


def test_dialogo_sigue_preguntando_filas_ambiguas_y_sin_equipo_con_valor():
    assert _siguiente_fila_pendiente(Parte(id="P", filas=[_fila(candidatos=["B-101", "B-102"])])) == 0
    assert _siguiente_fila_pendiente(Parte(id="P", filas=[_fila(valor=3.0)])) == 0


# --- equipos mencionados en una nota -------------------------------------------------------

class HttpPlanta:
    def equipos(self):
        return {"equipos": [
            {"tag": "B-101", "tipo": "bomba", "nombres": ["la uno", "la 1", "la de la punta"]},
            {"tag": "B-102", "tipo": "bomba", "nombres": ["la dos", "la 2", "la del medio"]},
            {"tag": "F-201A", "tipo": "filtro", "nombres": ["el filtro A", "el primario"]},
        ]}


def test_equipos_mencionados_en_la_transcripcion():
    r = ResolverNombres(HttpPlanta())
    assert r.equipos_mencionados(
        "La 2 pierde bastante por la empaquetadura, nota para el sistema, aprobala") == ["B-102"]
    assert set(r.equipos_mencionados("la del medio está en 1640, el filtro A me dio 1.1")) == {"B-102", "F-201A"}
    assert r.equipos_mencionados("la bomba hace ruido") == []  # tipo genérico no cuenta
    assert r.tags() == ["B-101", "B-102", "F-201A"]
