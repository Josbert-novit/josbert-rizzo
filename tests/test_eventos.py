from datetime import datetime, timedelta, timezone

from planta_contrato import Calidad, LecturaSensor, TipoEvento

from sistema.dominio.parte import EstadoParte, FilaParteInterna, Parte
from sistema.procesamiento.eventos import DetectorSensores, evaluar_parte
from sistema.procesamiento.limites import CatalogoLimites

SENSORES = [
    {"tag": "B-102", "variable": "presion_descarga", "unidad": "kg/cm2", "alarma_alta": 125, "alarma_baja": 95},
    {"tag": "B-101", "variable": "temperatura_aceite", "unidad": "degC", "alarma_alta": 80, "disparo": 90},
]


class HttpFalso:
    def __init__(self, bombas: dict[str, str] | None = None) -> None:
        self._bombas = bombas or {}

    def equipos(self):
        return {"sensores": SENSORES}

    def historial(self, tag, variable, desde, hasta):
        return []

    def estado(self):
        return {"bombas": [{"tag": tag, "estado": estado} for tag, estado in self._bombas.items()]}


AHORA = datetime.now(timezone.utc)


def _parte_con(filas: list[FilaParteInterna], parte_id: str = "PARTE-1") -> Parte:
    return Parte(id=parte_id, nota_ids=["NOTA-1"], estado=EstadoParte.confirmado, filas=filas)


def test_evaluar_parte_sin_novedad():
    fila = FilaParteInterna(tag_dicho="la dos", tag="B-102", variable="presion_descarga", valor=112.0, unidad="kg/cm2", hora=AHORA)
    catalogo = CatalogoLimites(HttpFalso())

    evento = evaluar_parte(_parte_con([fila]), catalogo)

    assert evento.tipo == TipoEvento.sin_novedad
    assert evento.origen == "parte"
    assert evento.id == "EVT-PARTE-PARTE-1"


def test_evaluar_parte_escalado_por_valor_fuera_de_limite():
    fila = FilaParteInterna(tag_dicho="la dos", tag="B-102", variable="presion_descarga", valor=140.0, unidad="kg/cm2", hora=AHORA)
    catalogo = CatalogoLimites(HttpFalso())

    evento = evaluar_parte(_parte_con([fila]), catalogo)

    assert evento.tipo == TipoEvento.escalado
    assert evento.equipo == "B-102"
    assert "B-102" in evento.resumen


def test_evaluar_parte_convierte_unidad_antes_de_comparar():
    # 140 kg/cm2 equivalente en psi no debería escalar falsamente si no excede el límite real.
    fila_normal = FilaParteInterna(
        tag_dicho="la dos", tag="B-102", variable="presion_descarga", valor=112.0 * 14.2233, unidad="psi", hora=AHORA
    )
    catalogo = CatalogoLimites(HttpFalso())

    evento = evaluar_parte(_parte_con([fila_normal]), catalogo)

    assert evento.tipo == TipoEvento.sin_novedad


def test_evaluar_parte_fila_sin_valor_no_es_anomalia():
    # Equipo parado/en reserva: no hay lectura que evaluar.
    fila = FilaParteInterna(
        tag_dicho="la tres", tag="B-103", variable="presion_descarga", valor=None,
        observacion="parada, en reserva", hora=AHORA,
    )
    catalogo = CatalogoLimites(HttpFalso())

    evento = evaluar_parte(_parte_con([fila]), catalogo)

    assert evento.tipo == TipoEvento.sin_novedad


def test_evaluar_parte_id_estable():
    fila = FilaParteInterna(tag_dicho="la dos", tag="B-102", variable="presion_descarga", valor=112.0, unidad="kg/cm2", hora=AHORA)
    catalogo = CatalogoLimites(HttpFalso())
    parte = _parte_con([fila])

    e1 = evaluar_parte(parte, catalogo)
    e2 = evaluar_parte(parte, catalogo)

    assert e1.id == e2.id


def _lectura(tag, variable, valor, ts, calidad=Calidad.buena) -> LecturaSensor:
    return LecturaSensor(tag=tag, variable=variable, valor=valor, unidad="kg/cm2", ts=ts, calidad=calidad)


def test_detector_no_escala_pico_aislado():
    catalogo = CatalogoLimites(HttpFalso())
    detector = DetectorSensores(catalogo)
    ts = AHORA

    evento = detector.procesar(_lectura("B-102", "presion_descarga", 140.0, ts))
    assert evento is None
    # Vuelve a la normalidad enseguida: no debería haber escalado nunca.
    evento = detector.procesar(_lectura("B-102", "presion_descarga", 112.0, ts + timedelta(seconds=10)))
    assert evento is None


def test_detector_escala_anomalia_sostenida():
    catalogo = CatalogoLimites(HttpFalso())
    detector = DetectorSensores(catalogo)
    ts = AHORA

    assert detector.procesar(_lectura("B-102", "presion_descarga", 140.0, ts)) is None
    evento = detector.procesar(_lectura("B-102", "presion_descarga", 141.0, ts + timedelta(seconds=310)))

    assert evento is not None
    assert evento.tipo == TipoEvento.escalado
    assert evento.origen == "sensor"
    assert evento.equipo == "B-102"


def test_detector_no_repite_evento_de_la_misma_racha():
    catalogo = CatalogoLimites(HttpFalso())
    detector = DetectorSensores(catalogo)
    ts = AHORA

    detector.procesar(_lectura("B-102", "presion_descarga", 140.0, ts))
    primero = detector.procesar(_lectura("B-102", "presion_descarga", 141.0, ts + timedelta(seconds=310)))
    segundo = detector.procesar(_lectura("B-102", "presion_descarga", 142.0, ts + timedelta(seconds=320)))

    assert primero is not None
    assert segundo is None


def test_detector_nivel_disparo_escala_mas_rapido():
    catalogo = CatalogoLimites(HttpFalso())
    detector = DetectorSensores(catalogo)
    ts = AHORA

    assert detector.procesar(_lectura("B-101", "temperatura_aceite", 95.0, ts)) is None
    evento = detector.procesar(_lectura("B-101", "temperatura_aceite", 96.0, ts + timedelta(seconds=65)))

    assert evento is not None


def test_detector_equipo_parado_no_es_anomalia():
    catalogo = CatalogoLimites(HttpFalso())
    detector = DetectorSensores(catalogo)
    ts = AHORA

    evento = detector.procesar(_lectura("B-102", "presion_descarga", None, ts, calidad=Calidad.sin_dato))

    assert evento is None


def test_detector_bomba_parada_no_escala_por_presion_baja():
    # B-103 en reserva: presión de descarga baja (1.5) no es anomalía, aunque esté bajo alarma_baja.
    sensores_b103 = SENSORES + [
        {"tag": "B-103", "variable": "presion_descarga", "unidad": "kg/cm2", "alarma_alta": 125, "alarma_baja": 95}
    ]

    class HttpB103(HttpFalso):
        def equipos(self):
            return {"sensores": sensores_b103}

    http = HttpB103(bombas={"B-103": "parada"})
    catalogo = CatalogoLimites(http)
    detector = DetectorSensores(catalogo, http)
    ts = AHORA

    assert detector.procesar(_lectura("B-103", "presion_descarga", 1.5, ts)) is None
    evento = detector.procesar(_lectura("B-103", "presion_descarga", 1.5, ts + timedelta(seconds=310)))

    assert evento is None


def test_detector_bomba_en_marcha_si_escala_por_presion_baja():
    sensores_b103 = SENSORES + [
        {"tag": "B-103", "variable": "presion_descarga", "unidad": "kg/cm2", "alarma_alta": 125, "alarma_baja": 95}
    ]

    class HttpB103(HttpFalso):
        def equipos(self):
            return {"sensores": sensores_b103}

    http = HttpB103(bombas={"B-103": "marcha"})
    catalogo = CatalogoLimites(http)
    detector = DetectorSensores(catalogo, http)
    ts = AHORA

    assert detector.procesar(_lectura("B-103", "presion_descarga", 1.5, ts)) is None
    evento = detector.procesar(_lectura("B-103", "presion_descarga", 1.5, ts + timedelta(seconds=310)))

    assert evento is not None
    assert evento.tipo == TipoEvento.escalado
