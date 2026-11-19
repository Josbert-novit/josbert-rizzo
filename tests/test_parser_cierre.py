"""Parser determinístico del cierre (G): textos reales del simulador y de cierres_ejemplo.json."""

import json
from pathlib import Path

import pytest

from sistema.procesamiento.parser_cierre import parsear_cierre

_EJEMPLOS = Path(__file__).resolve().parents[2] / "academia2026-tp/datos/documentos/contrato/cierres_ejemplo.json"


def test_cierre_real_de_la_corrida_g1():
    """Transcripción real (corrida g1-tarea-estandar): el LLM le había inventado T-B-ROD, I-VIB y 'C48'."""
    texto = ("Orden ORD EVT SENSOR F 201A presion_diferencial 2026 11 04T08:17:40 03:00. Hice cambio de "
             "cartuchos. Usé cuarenta y ocho cartucho filtrante 25 µm. Quedó en servicio, el diferencial "
             "está normal. Fueron tres y medio horas.")
    c = parsear_cierre(texto, ["FC-CART-25"])
    assert c.tareas == ["T-F-CAR"]
    assert [(r.codigo, r.cantidad) for r in c.repuestos] == [("FC-CART-25", 48)]
    assert c.horas == 3.5
    assert c.resultado_prueba.startswith("Quedó en servicio")


def test_bomba_con_prueba_y_kit():
    texto = ("Cierro la orden ORD 1. Hice cambio de empaquetadura. Usé un kit de empaquetadura TX-350. "
             "Probé a ciento diez kilos, sin pérdida. Fueron cuatro coma dos horas.")
    c = parsear_cierre(texto)
    assert c.tareas == ["T-B-EMP"]
    assert [(r.codigo, r.cantidad) for r in c.repuestos] == [("TX-EMP-KIT", 1)]
    assert c.horas == 4.2
    assert c.resultado_prueba == "Probé a ciento diez kilos, sin pérdida"


def test_la_cantidad_es_la_del_principio_no_el_numero_del_nombre():
    c = parsear_cierre("Hice cambio de cartuchos. Usé cuarenta y ocho cartucho filtrante 10 µm.", ["FC-CART-10"])
    assert [(r.codigo, r.cantidad) for r in c.repuestos] == [("FC-CART-10", 48)]


def test_sin_tarea_reconocida_devuelve_none_para_ir_al_llm():
    assert parsear_cierre("Listo, terminé lo de la bomba.") is None
    assert parsear_cierre("") is None


def _casos():
    if not _EJEMPLOS.exists():
        pytest.skip("academia2026-tp no está al lado de este repo")
    return json.loads(_EJEMPLOS.read_text(encoding="utf-8"))


def test_todos_los_cierres_de_ejemplo():
    for caso in _casos():
        c = parsear_cierre(caso["cierre_texto"], [r["codigo"] for r in caso["repuestos_orden"]])
        assert c is not None, caso["orden_id"]
        assert c.tareas == caso["tareas"], caso["orden_id"]
        declarados = sorted((r["codigo"], r["cantidad"]) for r in caso["repuestos_declarados"])
        assert sorted((r.codigo, r.cantidad) for r in c.repuestos) == declarados, caso["orden_id"]
        assert c.horas == caso["horas_declaradas"], caso["orden_id"]


@pytest.mark.parametrize("texto,horas", [
    ("Tiefro la orden oredévete sensores pero 101 apresión diferencial 2026 onces 0481910300 y se cambió de "
     "Cartuchos C48 Cartucho fitrante 25 micrones quedó en servicio, el diferencial está normal, fueron 2,8 horas",
     2.8),
    ("Cierro la orden al revés de sensores P201 a precio indiferencial 2026111814503300 y se cambio de cartuchos. "
     "C48K cartucho fitrante 25 micrones, quedó en servicio, el diferencial está normal, fueron 3 con 2 horas.",
     3.2),
])
def test_transcripciones_reales_de_whisper(texto, horas):
    """Audio real del cierre transcripto con el mismo Whisper del agente (corrida g1b-parser-cierre):
    'Hice' llega como 'y se', 'Usé cuarenta y ocho' como 'C48', 'filtrante' como 'fitrante'."""
    c = parsear_cierre(texto, ["FC-CART-25"])
    assert c is not None and c.tareas == ["T-F-CAR"]
    assert [(r.codigo, r.cantidad) for r in c.repuestos] == [("FC-CART-25", 48)]
    assert c.horas == horas
    assert c.resultado_prueba.lower().startswith("quedó en servicio")
