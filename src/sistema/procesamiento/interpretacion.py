"""Interpreta un texto (una nota de voz transcripta, o la corrección que dicta el operador en la
tablet) y lo convierte en filas de parte: extracción con LLM + resolución de nombres + unidades +
rango histórico. Se usa tanto para la nota original (1.1-1.6) como para reinterpretar una
corrección del operador (1.7), para no duplicar el pipeline."""

from datetime import datetime

from sistema.dominio.parte import FilaParteInterna
from sistema.integraciones.planta_http import PlantaHTTP
from sistema.llm.control import ConsumoTokens
from sistema.llm.extraccion import Extractor
from sistema.procesamiento.rango_historico import VerificadorRango
from sistema.procesamiento.resolucion import ResolverNombres, ResultadoResolucion
from sistema.procesamiento.unidades import resolver_unidad
from sistema.procesamiento.variables import mapear_variable


def interpretar_texto(
    texto: str,
    hora: datetime,
    fotos: list[str],
    extractor: Extractor,
    resolver: ResolverNombres,
    verificador_rango: VerificadorRango,
    consumo: ConsumoTokens | None = None,
) -> list[FilaParteInterna]:
    extraidas = extractor.extraer(texto, consumo)
    filas = []
    for fila in extraidas.filas:
        resolucion = resolver.resolver(fila.tag)
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
