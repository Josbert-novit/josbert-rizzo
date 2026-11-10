from datetime import datetime, timezone

from sistema.procesamiento.rango_historico import VerificadorRango


class HttpFalso:
    def __init__(self, historial: list[dict]) -> None:
        self.historial_devuelto = historial
        self.pedidos: list[tuple] = []

    def historial(self, tag, variable, desde, hasta):
        self.pedidos.append((tag, variable, desde, hasta))
        return self.historial_devuelto


HISTORIAL_B102 = [{"ts": "2026-11-01T00:00:00", "valor": v} for v in [110, 112, 114, 113, 111]]


def test_valor_dentro_de_rango_no_pide_confirmacion():
    http = HttpFalso(HISTORIAL_B102)
    v = VerificadorRango(http)

    fuera, motivo = v.verificar("B-102", "presion_descarga", 112.5, "kg/cm2", datetime.now(timezone.utc))

    assert fuera is False
    assert motivo is None


def test_valor_fuera_de_rango_pide_confirmacion():
    http = HttpFalso(HISTORIAL_B102)
    v = VerificadorRango(http)

    fuera, motivo = v.verificar("B-102", "presion_descarga", 300.0, "kg/cm2", datetime.now(timezone.utc))

    assert fuera is True
    assert "fuera del rango histórico" in motivo


def test_convierte_unidad_antes_de_comparar():
    # El historial está en kg/cm2; un valor en psi equivalente a ~112 kg/cm2 no debería disparar.
    http = HttpFalso(HISTORIAL_B102)
    v = VerificadorRango(http)
    valor_psi = 112.5 * 14.2233  # equivalente a 112.5 kg/cm2 en psi

    fuera, motivo = v.verificar("B-102", "presion_descarga", valor_psi, "psi", datetime.now(timezone.utc))

    assert fuera is False


def test_sin_historial_no_bloquea():
    http = HttpFalso([])
    v = VerificadorRango(http)

    fuera, motivo = v.verificar("TK-301", "nivel", 500.0, None, datetime.now(timezone.utc))

    assert fuera is False
    assert motivo is None


def test_cachea_el_historial_por_tag_y_variable(monkeypatch):
    http = HttpFalso(HISTORIAL_B102)
    v = VerificadorRango(http)
    ahora = datetime.now(timezone.utc)

    v.verificar("B-102", "presion_descarga", 112.0, "kg/cm2", ahora)
    v.verificar("B-102", "presion_descarga", 113.0, "kg/cm2", ahora)

    assert len(http.pedidos) == 1
