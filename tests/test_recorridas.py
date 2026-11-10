from datetime import datetime, timezone

from planta_contrato import FilaParte, NotaRecorrida

from sistema.config import config
from sistema.dominio.parte import EstadoParte
from sistema.llm.extraccion import FilasExtraidas
from sistema.procesamiento.recorridas import recibir_nota


class HttpFalso:
    def __init__(self) -> None:
        self.pedidos: list[str] = []

    def descargar(self, url_relativa, destino):
        self.pedidos.append(url_relativa)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"contenido-falso")
        return destino


class TranscriptorFalso:
    def __init__(self, texto: str = "la dos en ciento doce de descarga") -> None:
        self.texto = texto
        self.pedidos: list = []

    def transcribir(self, audio_path):
        self.pedidos.append(audio_path)
        return self.texto


class ExtractorFalso:
    def __init__(self, filas: list[FilaParte] | None = None) -> None:
        self.filas = filas if filas is not None else [
            FilaParte(tag="la dos", variable="presion_descarga", valor=112.0, unidad="kg/cm2")
        ]
        self.textos: list[str] = []

    def extraer(self, texto: str, consumo=None) -> FilasExtraidas:
        self.textos.append(texto)
        return FilasExtraidas(filas=self.filas)


class ResolverFalso:
    """Resuelve "la dos" -> B-102; cualquier otro nombre queda sin resolver."""

    EQUIPOS = {"B-102": {"tag": "B-102", "tipo": "bomba", "manometro_local": {"unidad": "kg/cm2"}}}

    def __init__(self) -> None:
        self.pedidos: list[str] = []

    def resolver(self, dicho: str):
        from sistema.procesamiento.resolucion import Resolucion, ResultadoResolucion

        self.pedidos.append(dicho)
        if dicho == "la dos":
            return Resolucion(ResultadoResolucion.resuelto, tag="B-102")
        return Resolucion(ResultadoResolucion.no_encontrado)

    def equipo_por_tag(self, tag: str):
        return self.EQUIPOS.get(tag)


class VerificadorRangoFalso:
    """Siempre dice que el valor está dentro de rango, salvo que se le pida lo contrario."""

    def __init__(self, fuera_de_rango: bool = False) -> None:
        self.fuera_de_rango = fuera_de_rango
        self.pedidos: list[tuple] = []

    def verificar(self, tag, variable_canonica, valor, unidad, hasta):
        self.pedidos.append((tag, variable_canonica, valor, unidad))
        if self.fuera_de_rango:
            return True, f"{valor} fuera de rango"
        return False, None


def test_recibir_nota_descarga_transcribe_y_extrae_parte(tmp_path, monkeypatch):
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
    transcriptor = TranscriptorFalso()
    extractor = ExtractorFalso()
    resolver = ResolverFalso()
    verificador_rango = VerificadorRangoFalso()

    parte = recibir_nota(nota, http, transcriptor, extractor, resolver, verificador_rango)
    carpeta = config.media_dir / nota.id

    assert (carpeta / "audio.wav").exists()
    assert (carpeta / "foto_0.jpg").exists()
    assert (carpeta / "foto_1.jpg").exists()
    assert (carpeta / "transcripcion.txt").read_text(encoding="utf-8") == transcriptor.texto
    assert http.pedidos == ["/media/nota-1.wav", "/media/foto-1.jpg", "/media/foto-2.jpg"]
    assert extractor.textos == [transcriptor.texto]
    assert resolver.pedidos == ["la dos"]
    assert verificador_rango.pedidos == [("B-102", "presion_descarga", 112.0, "kg/cm2")]

    assert parte.id == "PARTE-NOTA-1"
    assert parte.nota_ids == ["NOTA-1"]
    assert parte.estado == EstadoParte.borrador
    assert len(parte.filas) == 1
    fila = parte.filas[0]
    assert fila.tag_dicho == "la dos"
    assert fila.tag == "B-102"
    assert fila.ambiguo is False
    assert fila.valor == 112.0
    assert fila.unidad == "kg/cm2"
    assert fila.requiere_confirmacion is False
    assert fila.hora == nota.ts
    assert fila.fotos == nota.fotos
    assert (carpeta / "parte.json").exists()


def test_recibir_nota_vivo_sin_audio_ni_fotos_ni_filas(tmp_path, monkeypatch):
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
    transcriptor = TranscriptorFalso()
    extractor = ExtractorFalso(filas=[])
    resolver = ResolverFalso()
    verificador_rango = VerificadorRangoFalso()

    parte = recibir_nota(nota, http, transcriptor, extractor, resolver, verificador_rango)
    carpeta = config.media_dir / nota.id

    assert carpeta.exists()
    assert not (carpeta / "transcripcion.txt").exists()
    assert http.pedidos == []
    assert transcriptor.pedidos == []
    assert extractor.textos == [""]
    assert resolver.pedidos == []
    assert verificador_rango.pedidos == []
    assert parte.filas == []


def test_recibir_nota_fuera_de_rango_requiere_confirmacion(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "media_dir", tmp_path)
    nota = NotaRecorrida(
        id="NOTA-2",
        ts=datetime.now(timezone.utc),
        operador="Juan",
        audio_url=None,
        fotos=[],
        recorrida_id="REC-2",
    )
    http = HttpFalso()
    transcriptor = TranscriptorFalso()
    extractor = ExtractorFalso()
    resolver = ResolverFalso()
    verificador_rango = VerificadorRangoFalso(fuera_de_rango=True)

    parte = recibir_nota(nota, http, transcriptor, extractor, resolver, verificador_rango)

    fila = parte.filas[0]
    assert fila.requiere_confirmacion is True
    assert "fuera de rango" in fila.motivo_confirmacion
