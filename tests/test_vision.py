import pytest
from pydantic import ValidationError

from sistema.llm.vision import AnalisisFoto, analizar_foto


class ClienteFalso:
    def __init__(self, respuesta: AnalisisFoto) -> None:
        self.respuesta = respuesta
        self.urls: list[str] = []

    def analizar(self, url: str) -> AnalisisFoto:
        self.urls.append(url)
        return self.respuesta


def test_analizar_foto_delega_al_cliente_inyectado():
    cliente = ClienteFalso(AnalisisFoto(danio_estructural=True, descripcion="grieta visible"))

    resultado = analizar_foto("http://sim:8100/fotos/1.jpg", cliente=cliente)

    assert resultado.danio_estructural is True
    assert cliente.urls == ["http://sim:8100/fotos/1.jpg"]


def test_analisis_sin_dano_estructural():
    cliente = ClienteFalso(AnalisisFoto(danio_estructural=False, descripcion="desgaste normal de pintura"))

    resultado = analizar_foto("http://sim:8100/fotos/2.jpg", cliente=cliente)

    assert resultado.danio_estructural is False


def test_analisis_foto_no_acepta_campos_extra():
    with pytest.raises(ValidationError):
        AnalisisFoto(danio_estructural=False, descripcion="ok", campo_inventado="x")


def test_nemotron_vl_va_sin_razonamiento_y_otros_modelos_no():
    """Nemotron VL razona antes de contestar: con el razonamiento prendido se come max_tokens y
    `content` llega vacío (probado en vivo el 20/11). A otros modelos no se les manda el parámetro."""
    from sistema.llm.vision import ClienteNvidiaNIM

    uri = "data:image/jpeg;base64,AAAA"
    nemotron = ClienteNvidiaNIM(api_key="x", modelo="nvidia/nemotron-3.5-super-vl-120b-a12b")._cuerpo(uri)
    assert nemotron["chat_template_kwargs"] == {"enable_thinking": False}
    otro = ClienteNvidiaNIM(api_key="x", modelo="google/gemma-3-12b-it")._cuerpo(uri)
    assert "chat_template_kwargs" not in otro


def test_respuesta_sin_contenido_es_un_error_claro(monkeypatch):
    import httpx

    from sistema.llm import vision

    respuesta = httpx.Response(200, json={"choices": [{"message": {"content": None}}]},
                               request=httpx.Request("POST", "http://x"))
    monkeypatch.setattr(vision.httpx, "post", lambda *a, **k: respuesta)
    with pytest.raises(ValueError, match="no devolvió contenido"):
        vision.ClienteNvidiaNIM(api_key="x", modelo="m").analizar("data:image/jpeg;base64,AAAA")
