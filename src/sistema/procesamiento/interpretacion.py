"""Interpreta un texto (una nota de voz transcripta, o la corrección que dicta el operador en la
tablet) y lo convierte en filas de parte: extracción con LLM + resolución de nombres + unidades +
rango histórico. Se usa tanto para la nota original (1.1-1.6) como para reinterpretar una
corrección del operador (1.7), para no duplicar el pipeline."""

import logging
import re
from datetime import datetime

from sistema.dominio.parte import FilaParteInterna
from sistema.integraciones.planta_http import PlantaHTTP
from sistema.llm.control import ConsumoTokens
from sistema.llm.extraccion import Extractor
from sistema.procesamiento.rango_historico import VerificadorRango
from sistema.procesamiento.resolucion import Resolucion, ResolverNombres, ResultadoResolucion
from sistema.procesamiento.unidades import resolver_unidad
from sistema.procesamiento.variables import mapear_variable

log = logging.getLogger(__name__)

# Lote 5: parser determinístico (planta_kit de la cátedra) antes del LLM. Lo configura el agente al
# arrancar (`configurar_parser`), con el catálogo de `GET /api/planta`. Sin configurar (tests
# viejos, scripts), todo sigue por el LLM como antes.
_parser = None


def configurar_parser(parser) -> None:
    global _parser
    _parser = parser


def parser_configurado():
    return _parser

# Piezas de un equipo, no equipos (hallazgo H4 de la Fase 15): en "la del medio está en 130 de
# descarga. El rodamiento me dio 53 grados", el LLM sacó "rodamiento" como si fuera otro equipo y
# el sistema le preguntó al operador "no encontré ningún equipo para rodamiento". El operador
# habla de la pieza del equipo que acaba de nombrar.
_COMPONENTE = re.compile(
    r"^\s*(?:el|la|los|las)?\s*(rodamientos?|sellos?|empaquetaduras?|prensaestopas?|motor|acoples?|"
    r"aceite|c[aá]rter|correas?|pi[nñ][oó]n|v[aá]lvulas?|[eé]mbolos?|cojinetes?|ret[eé]n|"
    r"man[oó]metro|cabezal|carcasa|eje)\s*$",
    re.IGNORECASE,
)


# Lote 3 (H13, escenario `ataque`): el LLM también saca frases que *contienen* una pieza
# ("foto de la empaquetadura", "pérdida por empaquetadura"), no solo la pieza a secas.
_MENCIONA_COMPONENTE = re.compile(
    r"\b(rodamientos?|sellos?|empaquetaduras?|paqueta ?duras?|prensaestopas?|motor|acoples?|aceite|"
    r"c[aá]rter|correas?|pi[nñ][oó]n|v[aá]lvulas?|[eé]mbolos?|cojinetes?|ret[eé]n|man[oó]metro|"
    r"cabezal|carcasa|eje)\b",
    re.IGNORECASE,
)


def es_componente(dicho: str | None) -> bool:
    return bool(dicho) and bool(_COMPONENTE.match(dicho))


def menciona_componente(dicho: str | None) -> bool:
    return bool(dicho) and bool(_MENCIONA_COMPONENTE.search(dicho))


def _heredar_equipo_si_es_componente(
    dicho: str | None,
    resolucion: Resolucion,
    tag_anterior: str | None,
    mencionados_en_la_nota: list[str] | None = None,
) -> Resolucion:
    """Solo si el nombre no se resolvió por sí mismo y habla de una pieza: toma el equipo de la
    fila anterior de la misma nota; si no hay, el **único** equipo nombrado en toda la
    transcripción ("La 2 pierde por la empaquetadura" → B-102). Si se nombran varios equipos y no
    hay fila anterior, queda como estaba."""
    if resolucion.resultado != ResultadoResolucion.no_encontrado or not menciona_componente(dicho):
        return resolucion
    if tag_anterior:
        return Resolucion(ResultadoResolucion.resuelto, tag=tag_anterior)
    if mencionados_en_la_nota and len(mencionados_en_la_nota) == 1:
        return Resolucion(ResultadoResolucion.resuelto, tag=mencionados_en_la_nota[0])
    return resolucion


def _preguntar_equipo_si_tiene_valor(resolucion: Resolucion, valor: float | None, todos: list[str]) -> Resolucion:
    """Lote 3 (H13): "No encontré ningún equipo para X. ¿Podés repetirlo?" era un callejón sin
    salida — el operador no repite, contesta "no te entendí", y la nota nunca se confirma. Si la
    fila trae un valor (hay algo que guardar), se pregunta con **opciones** (todos los equipos),
    que el operador sí sabe contestar. Sin valor no se pregunta nada (ver
    `dialogo._siguiente_fila_pendiente`)."""
    if resolucion.resultado == ResultadoResolucion.no_encontrado and valor is not None and todos:
        return Resolucion(ResultadoResolucion.ambiguo, candidatos=list(todos))
    return resolucion


_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")


def _digitos(valor: float) -> str:
    """Los dígitos significativos de un número, sin separador decimal ni ceros a la izquierda:
    1630 → "1630", 0.5 → "5", 1.2 → "12"."""
    texto = f"{valor:.6f}".rstrip("0").rstrip(".").replace(".", "")
    return texto.lstrip("0") or "0"


def numeros_del_texto(texto: str) -> list[float]:
    return [float(n.replace(",", ".")) for n in _NUMERO.findall(texto or "")]


def anclar_valor(valor: float | None, texto: str) -> float | None:
    """Lote 3 (hallazgo H11): el LLM local (1.5b) a veces deforma un número que Whisper transcribió
    bien: "1630 de descarga" → 1063, "5 metros" → 0.5. Si el valor extraído no aparece en la
    transcripción pero hay **un único** número en el texto con exactamente los mismos dígitos
    (en otro orden o con la coma corrida), se usa ese. Determinístico y conservador: si el valor
    ya está en el texto, si el texto no trae dígitos ("uno con dos") o si hay más de un candidato,
    no se toca."""
    if valor is None:
        return None
    numeros = numeros_del_texto(texto)
    if not numeros or any(abs(n - valor) < 1e-9 for n in numeros):
        return valor
    objetivo = sorted(_digitos(valor))
    candidatos = {n for n in numeros if sorted(_digitos(n)) == objetivo}
    return candidatos.pop() if len(candidatos) == 1 else valor


def interpretar_texto(
    texto: str,
    hora: datetime,
    fotos: list[str],
    extractor: Extractor,
    resolver: ResolverNombres,
    verificador_rango: VerificadorRango,
    consumo: ConsumoTokens | None = None,
) -> list[FilaParteInterna]:
    respaldo: list[FilaParteInterna] | None = None
    if _parser is not None:
        try:
            filas_parser, va_al_llm = _parser.analizar(texto, hora, fotos, verificador_rango)
        except Exception:
            log.exception("parser determinístico: falló, sigue el LLM")
            filas_parser, va_al_llm = None, True
        if filas_parser is not None and not va_al_llm:
            log.info("extraccion=parser filas=%d", len(filas_parser))
            return filas_parser
        respaldo = filas_parser
    log.info("extraccion=llm")
    try:
        extraidas = extractor.extraer(texto, consumo)
    except Exception:
        # Lote 5b: con el parser configurado, un LLM que falla o pasa su timeout no traba la nota
        # (ni las que esperan detrás): se usa lo que entendió el parser.
        if respaldo is None:
            raise
        log.exception("extracción con LLM falló: se usa lo que entendió el parser (%d filas)", len(respaldo))
        return respaldo
    filas = []
    tag_anterior: str | None = None
    mencionados = resolver.equipos_mencionados(texto)
    todos = resolver.tags()
    for fila in extraidas.filas:
        fila = fila.model_copy(update={"valor": anclar_valor(fila.valor, texto)})
        resolucion = _heredar_equipo_si_es_componente(
            fila.tag, resolver.resolver(fila.tag), tag_anterior, mencionados
        )
        resolucion = _preguntar_equipo_si_tiene_valor(resolucion, fila.valor, todos)
        if resolucion.tag:
            tag_anterior = resolucion.tag
        equipo = resolver.equipo_por_tag(resolucion.tag) if resolucion.tag else None
        unidad, requiere_confirmacion, motivo = resolver_unidad(
            fila.variable, fila.valor, fila.unidad, equipo
        )

        if resolucion.tag and fila.valor is not None and not requiere_confirmacion:
            canonica = mapear_variable(fila.variable)
            if canonica:
                fuera_de_rango, motivo_rango = verificador_rango.verificar(
                    resolucion.tag, canonica, fila.valor, unidad, hora
                )
                if fuera_de_rango:
                    requiere_confirmacion, motivo = True, motivo_rango

        filas.append(
            FilaParteInterna(
                tag_dicho=fila.tag,
                tag=resolucion.tag,
                ambiguo=resolucion.resultado == ResultadoResolucion.ambiguo,
                candidatos=resolucion.candidatos,
                variable=fila.variable,
                valor=fila.valor,
                unidad=unidad,
                observacion=fila.observacion,
                hora=hora,
                fotos=list(fotos),
                requiere_confirmacion=requiere_confirmacion,
                motivo_confirmacion=motivo,
            )
        )
    return filas
