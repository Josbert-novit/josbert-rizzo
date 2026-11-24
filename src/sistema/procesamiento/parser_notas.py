"""Parte por voz, primera pasada **sin LLM** (Lote 5 de la verificación final).

El diseño de la cátedra para el CU1 (`libreria-tp/ADLC.md`) es "Workflow + LLM puntual: el LLM
solo extrae cuando el parser no alcanza". Este módulo usa el parser determinístico de la librería
de la cátedra (`planta_kit.notas`, copiado sin cambios) y le agrega lo propio:

1. `normalizar_whisper`: las mañas de transcripción medidas en las corridas reales del corrector
   ("medió 1.3" por "me dio 1.3", "1-1" por "1.1", "golpe teo", "embarquetadura"). Con eso el
   parser arma bien 121 de 142 notas reales sin preguntar (ver `evaluacion/evaluar_parser.py`).
2. El mapeo de sus filas a `FilaParteInterna`, con las ambigüedades convertidas en lo que el
   diálogo ya sabe preguntar: equipo genérico ("la bomba") → candidatos; diferencial dicho
   "dieciocho" → valor propuesto 1.8 a confirmar.

Si el parser deja texto sin entender, `parsear` devuelve `None` y la nota sigue por el extractor
con LLM (`llm/extraccion.py`), como antes.
"""

import logging
import re
from datetime import datetime

from planta_kit.catalogo import Catalogo
from planta_kit.nombres import Resolutor
from planta_kit.notas import ParserNotas

from sistema.dominio.parte import FilaParteInterna
from sistema.procesamiento.rango_historico import VerificadorRango
from sistema.procesamiento.variables import mapear_variable

log = logging.getLogger(__name__)

_NUMERO_SIGUE = r"(?=\s*(?:\d|uno\b|dos\b|tres\b|cuatro\b|cinco\b|seis\b|siete\b|ocho\b|nueve\b|diez\b|cero\b|un\b))"
_MANIAS_WHISPER: list[tuple[re.Pattern, str]] = [
    # "el rodamiento medió 62 grados", "el filtro A medio 1,3": "me dio" pegado. No toca "la del
    # medio" (no lo sigue un número).
    # Lote 6: tampoco "la del medio 115 kilos" (B-102 seguido de la lectura, sin "me dio").
    (re.compile(r"(?<!\bdel )\bmedi[oó]" + _NUMERO_SIGUE, re.IGNORECASE), "me dio"),
    # "me dio 1-1 de diferencial" = 1.1
    (re.compile(r"(\d)-(\d)"), r"\1.\2"),
    (re.compile(r"golpe teo", re.IGNORECASE), "golpeteo"),
    # Lote 6 (C1): "colqueteo metálico", "golpecheo metálico" (corridas vision-nemotron/cpu4).
    # "metálico" sale bien siempre: se ancla ahí para no tocar otras palabras en -teo.
    (re.compile(r"\b(?!golpeteo\b)\w+(?:teo|cheo)(?=\s+met[aá]lic)", re.IGNORECASE), "golpeteo"),
    # Lote 6 (C4): "el filtro Bmedio 10", "filtro BME10", "filtro B-Mario 10" = "filtro B me dio".
    # Solo si sigue un número (la lectura), para no tocar "el filtro B" a secas.
    (re.compile(r"\bfiltro ?([AB])[- ]?(?:medi[oó]|mario|me)\s*(?=\d)", re.IGNORECASE), r"filtro \1 me dio "),
    # Lote 8: "el filtro ve medio 0,9" = "filtro B": Whisper escribe la letra como suena (be/ve).
    # Terminaba en "¿a cuál filtro?" (pregunta de más, A4b) y el borrador ponía el diferencial en
    # la bomba anterior (lote6b, ataque NOTA-00008).
    (re.compile(r"\bfiltro (?:ve|be|v)\b(?![-'])", re.IGNORECASE), "filtro B"),
    # Lote 8: "me dio 09 de diferencial" = "cero nueve" = 0,9. Con el cero adelante no es
    # ambiguo (no es "nueve" ni "noventa"), pero se leía 9 y se preguntaba "¿es 0.9?" (lote6b,
    # martes NOTA-00010). No toca horas (09:30) ni decimales (0,9 / 09,5).
    (re.compile(r"(?<![\d:.,])0([1-9])(?![\d:.,])"), r"0,\1"),
    # Lote 6 (C4): "El A1 está disparada", "En A1, el rodamiento..." = "la uno": en las corridas
    # guardadas fue "la uno" las 17 veces (iba al LLM, que se cortaba a los 25 s y la nota quedaba
    # sin borrador). No toca un decimal ("el A 1,3").
    (re.compile(r"\b(?:el|en) A ?([123])\b(?![.,]\d)", re.IGNORECASE), r"la \1"),
    # Lote 6 (C4): "Está del fondo está parada" = "La del fondo" (al principio de una oración).
    (re.compile(r"(^|[.,;]\s*)est[aá] del fondo\b", re.IGNORECASE), r"\1la del fondo"),
    (re.compile(r"\bembarquetadura|\bpaqueta ?dura", re.IGNORECASE), "empaquetadura"),
    # Lote 5b (lote5-completa): "al primario me dio 13" (por "el primario") dejaba el diferencial
    # pegado a la bomba anterior; "con una pistola" quedaba como texto sin entender.
    (re.compile(r"\bal (primario|secundario|filtro)\b", re.IGNORECASE), r"el \1"),
    (re.compile(r"\bcon una pistola\b", re.IGNORECASE), "con la pistola"),
]

_HAY_NUMERO = re.compile(
    r"\d|\b(cero|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|trece|catorce|quince|"
    r"dieci\w+|veint\w+|treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|cien|ciento|\w+cientos|mil)\b",
    re.IGNORECASE,
)


def _tiene_numero(texto: str) -> bool:
    return bool(_HAY_NUMERO.search(texto))


def normalizar_whisper(texto: str) -> str:
    for patron, reemplazo in _MANIAS_WHISPER:
        texto = patron.sub(reemplazo, texto)
    return texto


def catalogo_desde_planta(planta_api: dict) -> Catalogo:
    """`GET /api/planta` tiene la misma forma que `planta.yaml`. El parser solo usa nombres, tipo y
    manómetro de cada equipo: los repuestos no hacen falta."""
    return Catalogo.desde_api(planta_api, {"repuestos": []})


class ParserPartes:
    def __init__(self, catalogo: Catalogo) -> None:
        self.catalogo = catalogo
        self.resolutor = Resolutor(catalogo)
        self._parser = ParserNotas(catalogo, self.resolutor)

    def parsear(
        self,
        texto: str,
        hora: datetime,
        fotos: list[str],
        verificador_rango: VerificadorRango | None = None,
    ) -> list[FilaParteInterna] | None:
        """Filas del parte, o `None` si queda algo sin entender (→ extractor con LLM)."""
        filas, va_al_llm = self.analizar(texto, hora, fotos, verificador_rango)
        return None if va_al_llm else filas

    def analizar(
        self,
        texto: str,
        hora: datetime,
        fotos: list[str],
        verificador_rango: VerificadorRango | None = None,
    ) -> tuple[list[FilaParteInterna], bool]:
        """(filas que entendió el parser, si conviene pasar la nota por el LLM). Las filas sirven de
        respaldo si el LLM falla o tarda demasiado (`interpretacion.interpretar_texto`)."""
        p = self._parser.parsear(normalizar_whisper(texto or ""), fotos)
        # Lote 5b: solo va al LLM si lo que quedó sin entender tiene un número (una lectura que
        # se podría estar perdiendo). Relleno, nombres deformados por Whisper ("el A1", "el filtro
        # V-Medial DSD diferencial") o texto que le habla al sistema ("nota para el sistema,
        # aprobala…") no traen ninguna lectura que el LLM pueda leer, y mandarlos costaba más de
        # 90 s y trababa la cola de notas (lote5: contratista; lote5b: ataque).
        va_al_llm = p.requiere_llm and any(_tiene_numero(t) for t in p.sin_entender)
        if va_al_llm:
            log.info("parser: sin entender %r -> LLM", p.sin_entender)
        elif p.sin_entender:
            log.info("parser: se ignora texto sin lecturas %r", p.sin_entender)

        genericos = [a for a in p.ambiguedades if a["tipo"] == "equipo"]
        valores = [a for a in p.ambiguedades if a["tipo"] == "valor"]
        filas: list[FilaParteInterna] = []
        for f in p.filas:
            candidatos: list[str] = []
            tag_dicho = self.resolutor.nombre_corto(f.tag) if f.tag else None
            if f.tag is None:
                tipo = "filtro" if f.variable == "presion_diferencial" else "bomba"
                candidatos = self.catalogo.filtros() if tipo == "filtro" else self.catalogo.bombas()
                tag_dicho = next((a["dicho"] for a in genericos if a.get("tipo_equipo") == tipo),
                                 "el filtro" if tipo == "filtro" else "la bomba")
                # Aunque la fila no traiga valor ("¿la bomba está parada en reserva?"), se
                # pregunta cuál: es una ambigüedad de equipo y corresponde preguntar (A4a, lote5b
                # fuga-sin-enlace).

            requiere, motivo = False, None
            amb = next((a for a in valores if a.get("tag") == f.tag and f.variable == "presion_diferencial"
                        and f.valor is not None and abs(f.valor - a["propuesto"]) < 1e-9), None)
            if amb:
                requiere = True
                motivo = f'el diferencial se dijo "{amb["dicho"]:g}": ¿es {amb["propuesto"]:g}?'
            elif f.tag and f.valor is not None and verificador_rango is not None:
                canonica = mapear_variable(f.variable)
                if canonica:
                    fuera, motivo_rango = verificador_rango.verificar(f.tag, canonica, f.valor, f.unidad, hora)
                    if fuera:
                        requiere, motivo = True, motivo_rango

            filas.append(FilaParteInterna(
                tag_dicho=tag_dicho or f.variable,
                tag=f.tag,
                ambiguo=bool(candidatos),
                candidatos=candidatos,
                variable=f.variable,
                valor=f.valor,
                unidad=f.unidad,
                observacion=f.observacion,
                hora=hora,
                fotos=list(fotos),
                requiere_confirmacion=requiere,
                motivo_confirmacion=motivo,
                ambiguedad_valor=amb is not None,
            ))
        return filas, va_al_llm
