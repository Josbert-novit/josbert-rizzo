from datetime import datetime, timezone

from planta_contrato import TipoEvento
from planta_contrato import NotaRecorrida

from sistema.procesamiento.seguridad import construir_evento, construir_mensaje_tablet, detectar

# Frases canónicas reales (planta_sim/operador.py::nota_seguridad).
TEXTO_S1 = "¡Atención! Se rompió algo en la descarga de la dos, sale agua a presión para todos lados, me alejé."
TEXTO_S2 = "Hay olor a gas fuerte del lado del portón, viene del lado del separador."
TEXTO_S3 = "Sale humo del motor de la dos, hay olor a quemado."
TEXTO_S4 = "Mi compañero se golpeó la mano con la llave en el skid de filtros, está sangrando bastante."

TEXTOS_NORMALES = [
    "La dos está en ciento doce kilos de descarga.",
    "El filtro A me dio uno con cuatro de diferencial.",
    "La bomba está parada, en reserva.",
    "El pulmón está en cinco metros.",
    "La uno hace un poco de ruido pero nada raro.",
]


def _nota(nota_id: str = "NOTA-1") -> NotaRecorrida:
    return NotaRecorrida(
        id=nota_id, ts=datetime.now(timezone.utc), operador="Juan", audio_url="/media/x.wav",
        fotos=[], recorrida_id="REC-1",
    )


def test_detecta_s1_agua_a_presion():
    d = detectar(TEXTO_S1)
    assert d is not None
    assert d.codigo == "S1"


def test_detecta_s1_aunque_whisper_deforme_a_presion():
    # Transcripción real de Whisper en vivo (ver BUGS.md): "a presión" salió como "aprecian".
    texto_real = (
        "Atención, se rompió algo en la descarga de la del medio, sale agua, aprecian para "
        "todos lados, me alejé, saqué foto desde lejos."
    )
    d = detectar(texto_real)
    assert d is not None
    assert d.codigo == "S1"


def test_detecta_s2_olor_a_gas():
    d = detectar(TEXTO_S2)
    assert d is not None
    assert d.codigo == "S2"


def test_detecta_s3_humo():
    d = detectar(TEXTO_S3)
    assert d is not None
    assert d.codigo == "S3"


def test_detecta_s4_persona_lesionada():
    d = detectar(TEXTO_S4)
    assert d is not None
    assert d.codigo == "S4"


def test_no_detecta_en_notas_normales():
    for texto in TEXTOS_NORMALES:
        assert detectar(texto) is None, f"falsa alarma con: {texto!r}"


def test_detecta_sin_importar_tildes_ni_mayusculas():
    assert detectar("HAY OLOR A GAS") is not None
    assert detectar("hay olor a gas") is not None


def test_construir_evento_es_alerta_de_seguridad_idempotente():
    d = detectar(TEXTO_S1)
    nota = _nota("NOTA-9")

    e1 = construir_evento(d, nota)
    e2 = construir_evento(d, nota)

    assert e1.tipo == TipoEvento.alerta_seguridad
    assert e1.nota_ids == ["NOTA-9"]
    assert e1.id == e2.id


def test_construir_mensaje_tablet_no_espera_respuesta():
    d = detectar(TEXTO_S4)
    nota = _nota("NOTA-9")

    mensaje = construir_mensaje_tablet(d, nota)

    assert mensaje.espera_respuesta is False
    assert mensaje.nota_ids == ["NOTA-9"]
    assert "S4" in mensaje.texto
