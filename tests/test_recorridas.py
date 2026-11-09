from datetime import datetime, timezone

from planta_contrato import NotaRecorrida

from sistema.config import config
from sistema.procesamiento.recorridas import recibir_nota


class HttpFalso:
    def __init__(self) -> None:
        self.pedidos: list[str] = []

    def descargar(self, url_relativa, destino):
        self.pedidos.append(url_relativa)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"contenido-falso")
        return destino


def test_recibir_nota_descarga_audio_y_fotos(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "media_dir", tmp_path)
    nota = NotaRecorrida(
        id="NOTA-1",
        ts=datetime.now(timezone.utc),
        operador="Juan",
        audio_url="/media/nota-1.wav",
        fotos=["/media/foto-1.jpg", "/media/foto-2.jpg"],
        recorrida_id="REC-1",
    )
    http = HttpFalso()

    carpeta = recibir_nota(nota, http)

    assert (carpeta / "audio.wav").exists()
    assert (carpeta / "foto_0.jpg").exists()
    assert (carpeta / "foto_1.jpg").exists()
    assert http.pedidos == ["/media/nota-1.wav", "/media/foto-1.jpg", "/media/foto-2.jpg"]


def test_recibir_nota_vivo_sin_audio_ni_fotos(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "media_dir", tmp_path)
    nota = NotaRecorrida(
        id="VIVO-1",
        ts=datetime.now(timezone.utc),
        operador="Juan",
        audio_url=None,
        fotos=[],
        recorrida_id="REC-1",
    )
    http = HttpFalso()

    carpeta = recibir_nota(nota, http)

    assert carpeta.exists()
    assert http.pedidos == []
