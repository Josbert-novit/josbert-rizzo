from datetime import datetime, timezone

from sistema.dominio.parte import EstadoParte, FilaParteInterna, Parte
from sistema.llm.control import ConsumoTokens
from sistema.persistencia.memoria import _estado_a_json, _json_a_estado
from sistema.procesamiento.dialogo import EstadoDialogo

AHORA = datetime.now(timezone.utc)


def _estado_de_ejemplo() -> EstadoDialogo:
    fila = FilaParteInterna(
        tag_dicho="la bomba", tag=None, ambiguo=True, candidatos=["B-101", "B-102"],
        variable="presion_descarga", valor=112.0, hora=AHORA,
    )
    parte = Parte(id="PARTE-1", nota_ids=["NOTA-1"], estado=EstadoParte.borrador, filas=[fila])
    consumo = ConsumoTokens(pasos=2, tokens_prompt=50, tokens_respuesta=20)
    return EstadoDialogo(
        parte=parte, mensaje_id_pendiente="MSG-1", fila_en_pregunta=0,
        consumo=consumo, intentos_desambiguacion={0: 1},
    )


def test_roundtrip_conserva_el_parte():
    estado = _estado_de_ejemplo()

    recuperado = _json_a_estado(_estado_a_json(estado))

    assert recuperado.parte.id == "PARTE-1"
    assert recuperado.parte.nota_ids == ["NOTA-1"]
    assert recuperado.parte.estado == EstadoParte.borrador
    assert len(recuperado.parte.filas) == 1
    assert recuperado.parte.filas[0].tag_dicho == "la bomba"
    assert recuperado.parte.filas[0].candidatos == ["B-101", "B-102"]


def test_roundtrip_conserva_el_consumo_y_el_mensaje_pendiente():
    estado = _estado_de_ejemplo()

    recuperado = _json_a_estado(_estado_a_json(estado))

    assert recuperado.mensaje_id_pendiente == "MSG-1"
    assert recuperado.fila_en_pregunta == 0
    assert recuperado.consumo.pasos == 2
    assert recuperado.consumo.tokens_totales == 70


def test_roundtrip_conserva_intentos_desambiguacion_con_claves_enteras():
    estado = _estado_de_ejemplo()

    recuperado = _json_a_estado(_estado_a_json(estado))

    # El JSON vuelve con claves string ("0"), tienen que volver a ser int (índice de fila real).
    assert recuperado.intentos_desambiguacion == {0: 1}
    assert all(isinstance(k, int) for k in recuperado.intentos_desambiguacion)


def test_estado_a_json_es_serializable_de_verdad():
    import json

    estado = _estado_de_ejemplo()

    # No debe reventar: tiene que ser JSON plano (lo que se manda a `::jsonb`), no objetos Python.
    texto = json.dumps(_estado_a_json(estado))
    assert "PARTE-1" in texto
