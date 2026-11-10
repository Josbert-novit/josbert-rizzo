import pytest

from sistema.llm.control import (
    MAX_PASOS_LLM_POR_NOTA,
    ConsumoTokens,
    PresupuestoAgotado,
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
