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
