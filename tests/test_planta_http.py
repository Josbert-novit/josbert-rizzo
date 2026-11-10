import httpx
import pytest

from sistema.integraciones import planta_http as modulo
from sistema.integraciones.planta_http import PlantaHTTP, PlantaHTTPError


class RespuestaFalsa:
    def __init__(self, status_code: int, json_data=None, text: str | None = None) -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text if text is not None else ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


def test_get_exitoso_al_primer_intento(monkeypatch):
    llamadas = []

    def get_falso(url, params=None, timeout=None):
        llamadas.append(url)
        return RespuestaFalsa(200, {"ok": True})

    monkeypatch.setattr(modulo.httpx, "get", get_falso)
    http = PlantaHTTP(base_url="http://planta")

    datos = http.equipos()

    assert datos == {"ok": True}
    assert len(llamadas) == 1


def test_reintenta_ante_error_5xx_y_despues_funciona(monkeypatch):
    respuestas = iter([RespuestaFalsa(503), RespuestaFalsa(200, {"ok": True})])

    def get_falso(url, params=None, timeout=None):
        return next(respuestas)

    monkeypatch.setattr(modulo.httpx, "get", get_falso)
    monkeypatch.setattr(modulo.time, "sleep", lambda s: None)
    http = PlantaHTTP(base_url="http://planta")

    datos = http.equipos()

    assert datos == {"ok": True}


def test_agota_reintentos_y_levanta_error_propio(monkeypatch):
    def get_falso(url, params=None, timeout=None):
        return RespuestaFalsa(503)

    monkeypatch.setattr(modulo.httpx, "get", get_falso)
    monkeypatch.setattr(modulo.time, "sleep", lambda s: None)
    http = PlantaHTTP(base_url="http://planta")

    with pytest.raises(PlantaHTTPError):
        http.equipos()


def test_error_4xx_no_se_reintenta(monkeypatch):
    llamadas = []

    def get_falso(url, params=None, timeout=None):
        llamadas.append(url)
        return RespuestaFalsa(404)

    monkeypatch.setattr(modulo.httpx, "get", get_falso)
    http = PlantaHTTP(base_url="http://planta")

    with pytest.raises(httpx.HTTPStatusError):
        http.secciones("NO-EXISTE")

    assert len(llamadas) == 1


def test_fragmentos_parsea_jsonl(monkeypatch):
    texto = '{"id": "TX350-D §3.2", "doc": "TX350-D"}\n{"id": "historial OT-24-0001", "doc": "historial"}\n'

    def get_falso(url, params=None, timeout=None):
        assert url == "http://planta/documentos/fragmentos.jsonl"
        return RespuestaFalsa(200, text=texto)

    monkeypatch.setattr(modulo.httpx, "get", get_falso)
    http = PlantaHTTP(base_url="http://planta")

    fragmentos = http.fragmentos()

    assert len(fragmentos) == 2
    assert fragmentos[0]["id"] == "TX350-D §3.2"
    assert fragmentos[1]["doc"] == "historial"


def test_archivo_json(monkeypatch):
    def get_falso(url, params=None, timeout=None):
        assert url == "http://planta/documentos/evaluacion/retrieval.json"
        return RespuestaFalsa(200, [{"id": "lim-01"}])

    monkeypatch.setattr(modulo.httpx, "get", get_falso)
    http = PlantaHTTP(base_url="http://planta")

    assert http.archivo_json("evaluacion/retrieval.json") == [{"id": "lim-01"}]
