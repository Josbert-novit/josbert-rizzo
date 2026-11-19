import threading
import time

import pytest

from sistema.llm import control
from sistema.llm.control import (
    MAX_PASOS_LLM_POR_NOTA,
    ConsumoTokens,
    PresupuestoAgotado,
    analisis_orden_en_curso,
    ceder_turno_si_hay_orden_en_curso,
    config_de_callback,
    verificar_presupuesto,
)


def test_consumo_acumula_pasos_y_tokens():
    consumo = ConsumoTokens()

    consumo.registrar(10, 5)
    consumo.registrar(20, 8)

    assert consumo.pasos == 2
    assert consumo.tokens_prompt == 30
    assert consumo.tokens_respuesta == 13
    assert consumo.tokens_totales == 43


def test_verificar_presupuesto_no_molesta_por_debajo_del_limite():
    consumo = ConsumoTokens(pasos=MAX_PASOS_LLM_POR_NOTA - 1)
    verificar_presupuesto(consumo)  # no debería levantar


def test_verificar_presupuesto_levanta_al_llegar_al_limite():
    consumo = ConsumoTokens(pasos=MAX_PASOS_LLM_POR_NOTA)
    with pytest.raises(PresupuestoAgotado):
        verificar_presupuesto(consumo)


def test_config_de_callback_trae_un_callback():
    consumo = ConsumoTokens()
    cfg = config_de_callback(consumo)
    assert len(cfg["callbacks"]) == 1


def test_ceder_turno_no_espera_si_no_hay_orden_en_curso():
    analisis_orden_en_curso.clear()
    inicio = time.monotonic()
    ceder_turno_si_hay_orden_en_curso()
    assert time.monotonic() - inicio < 0.1


def test_ceder_turno_espera_hasta_que_la_orden_libera_el_turno():
    # Ver BUGS.md #27: una nota cede el turno de Ollama mientras haya un análisis de orden en
    # curso (más peso en la rúbrica), pero retoma apenas el evento se limpia.
    analisis_orden_en_curso.set()
    try:
        liberado = threading.Event()

        def liberar_pronto():
            time.sleep(0.2)
            analisis_orden_en_curso.clear()
            liberado.set()

        threading.Thread(target=liberar_pronto, daemon=True).start()
        inicio = time.monotonic()
        ceder_turno_si_hay_orden_en_curso()
        duracion = time.monotonic() - inicio

        assert liberado.is_set()
        assert duracion < control.ESPERA_MAXIMA_PRIORIDAD_ORDEN_S
    finally:
        analisis_orden_en_curso.clear()


def test_ceder_turno_no_espera_para_siempre_si_la_orden_nunca_libera(monkeypatch):
    monkeypatch.setattr(control, "ESPERA_MAXIMA_PRIORIDAD_ORDEN_S", 0.2)
    analisis_orden_en_curso.set()
    try:
        inicio = time.monotonic()
        ceder_turno_si_hay_orden_en_curso()
        assert time.monotonic() - inicio < 2.0
    finally:
        analisis_orden_en_curso.clear()
