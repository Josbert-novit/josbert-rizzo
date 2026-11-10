from sistema.procesamiento.limites import CatalogoLimites, nivel_anomalia

SENSORES = [
    {"tag": "B-102", "variable": "presion_descarga", "unidad": "kg/cm2", "alarma_alta": 125, "alarma_baja": 95},
    {"tag": "B-101", "variable": "temperatura_aceite", "unidad": "degC", "alarma_alta": 80, "disparo": 90},
    {"tag": "FT-401", "variable": "caudal", "unidad": "m3/d", "alarma_baja": 2200},
]


class HttpFalso:
    def equipos(self):
        return {"sensores": SENSORES}


def test_carga_y_busca_limite():
    catalogo = CatalogoLimites(HttpFalso())

    limite = catalogo.limite("B-102", "presion_descarga")

    assert limite.alarma_alta == 125
    assert limite.alarma_baja == 95


def test_tag_sin_sensor_no_tiene_limite():
    catalogo = CatalogoLimites(HttpFalso())

    assert catalogo.limite("TK-301", "nivel") is None


def test_nivel_anomalia_normal_dentro_de_rango():
    limite = CatalogoLimites(HttpFalso()).limite("B-102", "presion_descarga")
    assert nivel_anomalia(limite, 112.0) is None


def test_nivel_anomalia_alta():
    limite = CatalogoLimites(HttpFalso()).limite("B-102", "presion_descarga")
    assert nivel_anomalia(limite, 130.0) == "alarma"


def test_nivel_anomalia_baja():
    limite = CatalogoLimites(HttpFalso()).limite("B-102", "presion_descarga")
    assert nivel_anomalia(limite, 50.0) == "alarma"


def test_nivel_anomalia_disparo_mas_grave_que_alarma():
    limite = CatalogoLimites(HttpFalso()).limite("B-101", "temperatura_aceite")
    assert nivel_anomalia(limite, 85.0) == "alarma"
    assert nivel_anomalia(limite, 95.0) == "disparo"


def test_sin_limite_ni_valor_no_es_anomalia():
    assert nivel_anomalia(None, 999.0) is None
    limite = CatalogoLimites(HttpFalso()).limite("B-102", "presion_descarga")
    assert nivel_anomalia(limite, None) is None
