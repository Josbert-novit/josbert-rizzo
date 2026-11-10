from sistema.llm.control import ConsumoTokens
from sistema.llm.extraccion import Extractor, FilasExtraidas


class ChatFalso:
    def __init__(self, respuesta: FilasExtraidas) -> None:
        self.respuesta = respuesta
        self.llamadas: list = []
        self.configs: list = []

    def invoke(self, mensajes, config=None):
        self.llamadas.append(mensajes)
        self.configs.append(config)
        return self.respuesta


def test_extraer_con_texto_vacio_no_llama_al_modelo():
    chat = ChatFalso(FilasExtraidas(filas=[]))
    extractor = Extractor(chat=chat)

    resultado = extractor.extraer("")

    assert resultado.filas == []
    assert chat.llamadas == []


def test_extraer_con_texto_delega_al_chat():
    chat = ChatFalso(FilasExtraidas(filas=[]))
    extractor = Extractor(chat=chat)

    extractor.extraer("la dos en ciento doce")

    assert len(chat.llamadas) == 1
    mensajes = chat.llamadas[0]
    assert mensajes[1] == ("human", "la dos en ciento doce")


def test_extraer_sin_consumo_no_pasa_callbacks():
    chat = ChatFalso(FilasExtraidas(filas=[]))
    extractor = Extractor(chat=chat)

    extractor.extraer("la dos en ciento doce")

    assert chat.configs == [None]


def test_extraer_con_consumo_pasa_callback_de_registro():
    chat = ChatFalso(FilasExtraidas(filas=[]))
    extractor = Extractor(chat=chat)
    consumo = ConsumoTokens()

    extractor.extraer("la dos en ciento doce", consumo)

    assert chat.configs[0] is not None
    assert "callbacks" in chat.configs[0]
