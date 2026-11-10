from datetime import datetime, timezone

from planta_contrato import FilaParte

from sistema.dominio.parte import EstadoParte, FilaParteInterna, Parte
from sistema.llm.control import MAX_PASOS_LLM_POR_NOTA
from sistema.llm.extraccion import FilasExtraidas
from sistema.procesamiento.dialogo import (
    MAX_INTENTOS_DESAMBIGUACION,
    EstadoDialogo,
    construir_mensaje,
    procesar_respuesta,
)
from sistema.procesamiento.resolucion import Resolucion, ResultadoResolucion


class ExtractorFalso:
    def __init__(self, filas: list[FilaParte]) -> None:
        self.filas = filas
        self.llamadas = 0

    def extraer(self, texto: str, consumo=None) -> FilasExtraidas:
        self.llamadas += 1
        if consumo is not None:
            consumo.registrar(10, 5)
        return FilasExtraidas(filas=self.filas)


class ResolverFalso:
    def __init__(self, resultado: Resolucion | None = None) -> None:
        self.resultado = resultado or Resolucion(ResultadoResolucion.resuelto, tag="B-101")

    def resolver(self, dicho: str) -> Resolucion:
        return self.resultado

    def equipo_por_tag(self, tag: str):
        return None


class VerificadorRangoFalso:
    def verificar(self, tag, variable_canonica, valor, unidad, hasta):
        return False, None


AHORA = datetime.now(timezone.utc)


def _parte_con(filas: list[FilaParteInterna]) -> Parte:
    return Parte(id="PARTE-1", nota_ids=["NOTA-1"], estado=EstadoParte.borrador, filas=filas)


def test_construir_mensaje_pregunta_por_fila_ambigua():
    fila = FilaParteInterna(
        tag_dicho="la bomba", tag=None, ambiguo=True, candidatos=["B-101", "B-102"],
        variable="presion_descarga", valor=112.0, hora=AHORA,
    )
    estado = EstadoDialogo(parte=_parte_con([fila]))

    mensaje = construir_mensaje(estado)

    assert mensaje.pantalla.opciones == ["B-101", "B-102"]
    # No manda el borrador junto con la pregunta de opciones: si no, el operador (virtual o real)
    # puede confundir "elegir un equipo" con "revisar/corregir el parte completo" (ver BUGS.md #14).
    assert mensaje.pantalla.filas == []
    assert estado.fila_en_pregunta == 0
    assert estado.mensaje_id_pendiente == mensaje.id


def test_construir_mensaje_sin_pendientes_pide_confirmacion_final():
    fila = FilaParteInterna(tag_dicho="la uno", tag="B-101", variable="presion_descarga", valor=112.0, hora=AHORA)
    estado = EstadoDialogo(parte=_parte_con([fila]))

    mensaje = construir_mensaje(estado)

    assert "Confirmás" in mensaje.texto
    assert estado.fila_en_pregunta is None
    assert len(mensaje.pantalla.filas) == 1  # acá sí va el borrador completo, para que lo revisen


def test_responder_eligiendo_candidato_resuelve_la_fila():
    fila = FilaParteInterna(
        tag_dicho="la bomba", tag=None, ambiguo=True, candidatos=["B-101", "B-102"],
        variable="presion_descarga", valor=112.0, hora=AHORA,
    )
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=0)
    resolver = ResolverFalso(Resolucion(ResultadoResolucion.resuelto, tag="B-101"))

    confirmado = procesar_respuesta(estado, "la uno", ExtractorFalso([]), resolver, VerificadorRangoFalso())

    assert confirmado is False
    assert estado.parte.filas[0].tag == "B-101"
    assert estado.parte.filas[0].ambiguo is False


def test_confirmar_fila_con_requerimiento_la_deja_lista():
    fila = FilaParteInterna(
        tag_dicho="la uno", tag="B-101", variable="presion_descarga", valor=112.0,
        hora=AHORA, requiere_confirmacion=True, motivo_confirmacion="fuera de rango",
    )
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=0)

    confirmado = procesar_respuesta(estado, "si, confirmo", ExtractorFalso([]), ResolverFalso(), VerificadorRangoFalso())

    assert confirmado is False
    assert estado.parte.filas[0].requiere_confirmacion is False


def test_confirmacion_final_marca_parte_confirmado():
    fila = FilaParteInterna(tag_dicho="la uno", tag="B-101", variable="presion_descarga", valor=112.0, hora=AHORA)
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=None)

    confirmado = procesar_respuesta(estado, "sí, dale", ExtractorFalso([]), ResolverFalso(), VerificadorRangoFalso())

    assert confirmado is True
    assert estado.parte.estado == EstadoParte.confirmado


def test_correccion_en_confirmacion_final_actualiza_la_fila():
    fila = FilaParteInterna(tag_dicho="la uno", tag="B-101", variable="presion_descarga", valor=112.0, hora=AHORA)
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=None)
    correccion = FilaParte(tag="la uno", variable="presion_descarga", valor=115.0, unidad="kg/cm2")
    extractor = ExtractorFalso([correccion])
    resolver = ResolverFalso(Resolucion(ResultadoResolucion.resuelto, tag="B-101"))

    confirmado = procesar_respuesta(
        estado, "No, la uno estaba en ciento quince kilos", extractor, resolver, VerificadorRangoFalso()
    )

    assert confirmado is False
    assert estado.parte.estado == EstadoParte.borrador
    assert len(estado.parte.filas) == 1
    assert estado.parte.filas[0].valor == 115.0


def test_correccion_en_confirmacion_final_no_duplica_si_el_equipo_sigue_sin_resolver():
    # Ver BUGS.md #18: cuando ninguna de las dos filas tiene `tag` resuelto todavía, el merge
    # comparaba `tag_dicho` por igualdad exacta — si la corrección lo frasea con una variación
    # menor (acá, sin el artículo "el" adelante, algo que la extracción varía seguido entre
    # llamadas), no matcheaba y quedaba una fila vieja huérfana duplicada.
    fila = FilaParteInterna(
        tag_dicho="el filtro A", tag=None, variable="presion_diferencial", valor=4.0, hora=AHORA,
    )
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=None)
    correccion = FilaParte(tag="filtro A", variable="presion_diferencial", valor=1.4, unidad="kg/cm2")
    extractor = ExtractorFalso([correccion])
    resolver = ResolverFalso(Resolucion(ResultadoResolucion.no_encontrado))

    procesar_respuesta(estado, "no, el filtro A dio uno coma cuatro", extractor, resolver, VerificadorRangoFalso())

    assert len(estado.parte.filas) == 1  # no quedó la fila vieja duplicada
    assert estado.parte.filas[0].valor == 1.4


def test_correccion_registra_consumo_de_tokens():
    fila = FilaParteInterna(tag_dicho="la uno", tag="B-101", variable="presion_descarga", valor=112.0, hora=AHORA)
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=None)
    correccion = FilaParte(tag="la uno", variable="presion_descarga", valor=115.0, unidad="kg/cm2")
    extractor = ExtractorFalso([correccion])
    resolver = ResolverFalso(Resolucion(ResultadoResolucion.resuelto, tag="B-101"))

    procesar_respuesta(estado, "No, la uno estaba en ciento quince kilos", extractor, resolver, VerificadorRangoFalso())

    assert estado.consumo.pasos == 1
    assert estado.consumo.tokens_totales == 15


def test_presupuesto_agotado_deja_de_llamar_al_llm():
    fila = FilaParteInterna(
        tag_dicho="la uno", tag="B-101", variable="presion_descarga", valor=112.0,
        hora=AHORA, requiere_confirmacion=True, motivo_confirmacion="fuera de rango",
    )
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=0)
    estado.consumo.pasos = MAX_PASOS_LLM_POR_NOTA  # ya se gastó el presupuesto de esta nota
    extractor = ExtractorFalso([FilaParte(tag="la uno", variable="presion_descarga", valor=999.0)])
    resolver = ResolverFalso(Resolucion(ResultadoResolucion.resuelto, tag="B-101"))

    confirmado = procesar_respuesta(estado, "no, estaba mal", extractor, resolver, VerificadorRangoFalso())

    assert confirmado is False
    assert extractor.llamadas == 0
    assert estado.parte.filas[0].valor == 112.0  # no se reinterpretó: queda el valor original
    # No se deja la fila en requiere_confirmacion=True: eso volvería a preguntar lo mismo para
    # siempre, ya que (sin presupuesto) nunca se va a poder reinterpretar una corrección de nuevo.
    assert estado.parte.filas[0].requiere_confirmacion is False
    assert "revisar a mano" in estado.parte.filas[0].observacion


def test_correccion_de_fila_puntual_reemplaza_en_vez_de_duplicar():
    # Ver BUGS.md #16: antes esto fusionaba por tag_dicho/tag, y una corrección que la extracción
    # frasea distinto a la nota original (p. ej. "el de la izquierda" vs "el filtro A") no
    # matcheaba ninguna fila existente y quedaba como una fila nueva, duplicada, dejando la vieja
    # (con el valor incorrecto) también en el parte.
    fila = FilaParteInterna(
        tag_dicho="el filtro A", tag="F-201A", variable="presion_diferencial", valor=4.0,
        hora=AHORA, requiere_confirmacion=True, motivo_confirmacion="fuera de rango",
    )
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=0)
    correccion = FilaParte(tag="el de la izquierda", variable="presion_diferencial", valor=1.4, unidad="kg/cm2")
    extractor = ExtractorFalso([correccion])
    resolver = ResolverFalso(Resolucion(ResultadoResolucion.resuelto, tag="F-201A"))

    confirmado = procesar_respuesta(estado, "no, era uno coma cuatro", extractor, resolver, VerificadorRangoFalso())

    assert confirmado is False
    assert len(estado.parte.filas) == 1  # no quedó duplicada la fila vieja
    assert estado.parte.filas[0].valor == 1.4


def test_presupuesto_agotado_en_confirmacion_final_acepta_el_parte():
    # Ver BUGS.md #17: esta rama (fila_en_pregunta=None, confirmación final) se había quedado sin
    # el mismo freno que #13 le puso a la rama de confirmar una fila puntual.
    fila = FilaParteInterna(tag_dicho="la uno", tag="B-101", variable="presion_descarga", valor=112.0, hora=AHORA)
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=None)
    estado.consumo.pasos = MAX_PASOS_LLM_POR_NOTA
    extractor = ExtractorFalso([FilaParte(tag="la uno", variable="presion_descarga", valor=999.0)])

    confirmado = procesar_respuesta(estado, "no, eso no es así", extractor, ResolverFalso(), VerificadorRangoFalso())

    assert confirmado is True
    assert extractor.llamadas == 0
    assert estado.parte.estado == EstadoParte.confirmado
    assert "revisar a mano" in estado.parte.filas[0].observacion


def test_presupuesto_agotado_sin_encontrar_equipo_no_queda_en_loop_infinito():
    # Ver BUGS.md #17: esta rama (equipo no encontrado) tampoco tenía el freno de #13.
    fila = FilaParteInterna(
        tag_dicho="el rodamiento medio", tag=None, variable="temperatura", valor=59.0, hora=AHORA,
    )
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=0)
    estado.consumo.pasos = MAX_PASOS_LLM_POR_NOTA
    extractor = ExtractorFalso([])

    confirmado = procesar_respuesta(estado, "el rodamiento de la del medio", extractor, ResolverFalso(), VerificadorRangoFalso())

    assert confirmado is False
    assert extractor.llamadas == 0
    assert estado.parte.filas[0].tag == "el rodamiento medio"  # usa el dicho como tag literal
    assert "revisar a mano" in estado.parte.filas[0].observacion


def test_desambiguacion_que_nunca_resuelve_no_queda_en_loop_infinito():
    # Ver BUGS.md #15: una respuesta que el resolver nunca logra matchear (ASR ruidoso, frase
    # rara) no debe repetir la misma pregunta para siempre, porque esta rama no llama al LLM y
    # por lo tanto nunca agota el presupuesto de pasos por sí sola.
    fila = FilaParteInterna(
        tag_dicho="la bomba", tag=None, ambiguo=True, candidatos=["B-101", "B-102"],
        variable="presion_descarga", valor=112.0, hora=AHORA,
    )
    estado = EstadoDialogo(parte=_parte_con([fila]), fila_en_pregunta=0)
    resolver = ResolverFalso(Resolucion(ResultadoResolucion.ambiguo, candidatos=["B-101", "B-102"]))

    for _ in range(MAX_INTENTOS_DESAMBIGUACION):
        confirmado = procesar_respuesta(estado, "no te entiendo", ExtractorFalso([]), resolver, VerificadorRangoFalso())
        assert confirmado is False

    assert estado.parte.filas[0].tag == "B-101"  # se queda con el primer candidato, no sigue preguntando
    assert estado.parte.filas[0].ambiguo is False
    assert estado.parte.filas[0].candidatos == []
    assert "revisar a mano" in estado.parte.filas[0].observacion
