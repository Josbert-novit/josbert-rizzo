"""Novedades dichas de palabra (Lote 4, hallazgo H15): fallas que el operador cuenta en la nota de
voz sin un número que se pueda comparar contra un límite — "pierde bastante por la empaquetadura",
"hace un golpeteo metálico", "el visor de aceite está bajo", "la tres está disparada, no arranca".

Antes solo escalaba un parte con un **valor** fuera de límite, así que estas fallas terminaban en
`SinNovedad` y sin orden (B1, D2 y F3 bajos en la corrida lote3-completa). Igual que la alerta de
seguridad, es un matcheo determinístico de palabras sobre la transcripción (local, sin LLM, mismo
resultado siempre), con palabras completas o raíces largas para no repetir el error de H14.

Cada novedad se atribuye al equipo nombrado en el mismo tramo (oración o tramo entre comas) o, si
el tramo no nombra ninguno, al último nombrado antes. Si no se sabe ("la bomba gotea", que se
desambiguó en el diálogo), se usa el parte: la fila que habla de lo mismo o, si el parte es de un
solo equipo, ese.
"""

import re
import unicodedata

from sistema.dominio.parte import Parte

# Cosas que el operador solo dice cuando hay un problema. "parada, en reserva" y "parada por
# mantenimiento" NO son novedad (la consigna las lista como observaciones normales): por eso se
# busca "disparad", no "parad".
_PATRONES: list[tuple[str, str]] = [
    (r"empaquet|\bpaqueta", "pérdida por empaquetadura"),
    (r"\bgote(a|an|ando|o)\b|\bchorre(a|an|ando)\b|\bpierde(n)?\b", "pérdida"),
    (r"rebals", "rebalse a pileta"),
    (r"disparad|no arranca", "equipo disparado"),
    (r"\bvisor\b|casi no tiene aceite|aceite (esta )?bajo", "nivel de aceite bajo"),
    (r"\bcorreas?\b|\bchill(a|an|ando)\b", "correas en mal estado"),
    (r"golpe ?teo", "golpeteo metálico"),
    (r"\bpiedras?\b|cavit", "ruido de cavitación"),
    (r"\bruido\b", "ruido anormal"),
    # El operador solo le saca foto al manómetro del filtro con el diferencial alto (>= 1.5).
    (r"manometro", "diferencial alto"),
]

# Temperatura de rodamiento medida con la pistola: la consigna la da por caliente arriba de 75 °C.
TEMPERATURA_RODAMIENTO_CALIENTE = 75.0
_GRADOS = re.compile(r"(\d+(?:[.,]\d+)?)\s*(grados|°)")

# Separa oraciones sin cortar decimales ("1.8").
_FIN_DE_ORACION = re.compile(r"(?<!\d)[.!?;]|[.!?;](?!\d)")
# Tramos: oraciones y también comas (sin cortar "1,4"): una oración suele hablar de varios equipos.
_FIN_DE_TRAMO = re.compile(r"(?<!\d)[.!?;,]|[.!?;,](?!\d)")
# "la bomba" a secas: se habla de una bomba sin decir cuál — corta la herencia del tramo anterior.
_EQUIPO_GENERICO = re.compile(r"\b(la|una) bomba\b|\bel filtro\b(?! [ab]\b)")


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_tildes.lower()


def novedades_en(texto: str) -> list[str]:
    """Descripciones de las novedades que aparecen en un texto (vacía si no hay ninguna)."""
    normalizado = _normalizar(texto)
    vistas = [descripcion for patron, descripcion in _PATRONES if re.search(patron, normalizado)]
    for numero, _ in _GRADOS.findall(normalizado):
        if float(numero.replace(",", ".")) > TEMPERATURA_RODAMIENTO_CALIENTE:
            vistas.append("rodamiento caliente")
    return vistas


def oraciones(texto: str) -> list[str]:
    return [o.strip() for o in _FIN_DE_ORACION.split(texto) if o and o.strip()]


def _es_temperatura(fila) -> bool:
    texto = _normalizar(f"{fila.variable} {fila.unidad or ''}")
    return any(p in texto for p in ("temp", "rodam", "degc", "grado", "°c"))


def _por_tramos(texto: str, equipos_mencionados) -> tuple[dict[str, list[str]], bool]:
    """({tag: [novedades]} atribuidas seguro, hay alguna novedad que no se pudo atribuir)."""
    salida: dict[str, list[str]] = {}
    actual: str | None = None
    hay_dudosas = False
    for tramo in [t.strip() for t in _FIN_DE_TRAMO.split(texto or "") if t and t.strip()]:
        mencionados = list(dict.fromkeys(equipos_mencionados(tramo)))
        if mencionados:
            actual = mencionados[-1]
        elif _EQUIPO_GENERICO.search(_normalizar(tramo)):
            actual = None
        novedades = novedades_en(tramo)
        if not novedades:
            continue
        tag = mencionados[0] if len(mencionados) == 1 else (actual if not mencionados else None)
        if tag:
            salida.setdefault(tag, [])
            salida[tag].extend(n for n in novedades if n not in salida[tag])
        else:
            hay_dudosas = True
    return salida, hay_dudosas


def novedades_del_texto(texto: str, equipos_mencionados) -> dict[str, list[str]]:
    """{tag: [novedades]} solo con la transcripción, antes de armar el parte (Lote 4, L4.9): para
    escalar apenas llega la nota. Solo lo que se puede atribuir seguro (un equipo nombrado en el
    tramo, o heredado del tramo anterior); lo dudoso queda para cuando se confirma el parte.

    Hace falta porque el operador simulado solo confirma un borrador con filas **con valor**: una
    nota como "la dos pierde bastante por la empaquetadura" (sin números) no se confirma nunca, y
    si el evento esperara a la confirmación no habría ni Escalado ni orden (D2 en `ataque`)."""
    return _por_tramos(texto, equipos_mencionados)[0]


def equipos_sin_novedad_seguros(texto: str, equipos_mencionados) -> list[str]:
    """Equipos nombrados en la nota sin ninguna falla dicha (L4.11), para publicar SinNovedad sin
    esperar el parte. Si alguna falla de la nota no se pudo atribuir ("en la bomba el rodamiento
    me dio 96 grados", dos equipos en un mismo tramo), no se arriesga ninguno: lista vacía."""
    con_novedad, hay_dudosas = _por_tramos(texto, equipos_mencionados)
    if hay_dudosas:
        return []
    return [t for t in dict.fromkeys(equipos_mencionados(texto or "")) if t not in con_novedad]


def novedades_del_parte(parte: Parte, texto: str, equipos_mencionados) -> dict[str, list[str]]:
    """{tag: [novedades]} para los equipos del parte con alguna falla dicha de palabra.

    `equipos_mencionados(oracion) -> list[tag]` es `ResolverNombres.equipos_mencionados`."""
    tags_del_parte = {f.tag for f in parte.filas if f.tag}
    salida: dict[str, list[str]] = {}

    def anotar(tag: str, novedades: list[str]) -> None:
        if tag in tags_del_parte:
            salida.setdefault(tag, [])
            salida[tag].extend(n for n in novedades if n not in salida[tag])

    # 1) Por tramo de la transcripción, al equipo nombrado en ese tramo. Un tramo que no nombra
    # ninguno hereda el último nombrado: Whisper corta "la dos está en 1610. El rodamiento me dio
    # 66 grados. Hace un golpeteo metálico." en tres oraciones que hablan de la misma bomba.
    actual: str | None = None
    for tramo in [t.strip() for t in _FIN_DE_TRAMO.split(texto or "") if t and t.strip()]:
        mencionados = [t for t in equipos_mencionados(tramo) if t in tags_del_parte]
        if mencionados:
            actual = mencionados[-1]
        elif _EQUIPO_GENERICO.search(_normalizar(tramo)):
            actual = None
        novedades = novedades_en(tramo)
        if not novedades:
            continue
        if len(mencionados) == 1 or (not mencionados and actual):
            anotar(mencionados[0] if mencionados else actual, novedades)
            continue
        # Sin equipo conocido (o con varios) en el tramo: la fila del parte que habla de lo mismo.
        candidatas = {f.tag for f in parte.filas if f.tag and (not mencionados or f.tag in mencionados)
                      and novedades_en(f"{f.variable.replace('_', ' ')} {f.observacion or ''}")}
        if not candidatas and len(tags_del_parte) == 1:
            candidatas = set(tags_del_parte)
        for tag in candidatas:
            anotar(tag, novedades)

    # 2) Por fila del parte (lo que el LLM anotó como variable/observación, y temperaturas altas).
    for fila in parte.filas:
        if not fila.tag:
            continue
        novedades = novedades_en(f"{fila.variable.replace('_', ' ')} {fila.observacion or ''}")
        # La temperatura en número (no en "grados" dentro del texto) se mira acá.
        novedades = [n for n in novedades if n != "rodamiento caliente"]
        if fila.valor is not None and _es_temperatura(fila) and fila.valor > TEMPERATURA_RODAMIENTO_CALIENTE:
            novedades.append("rodamiento caliente")
        if novedades:
            anotar(fila.tag, novedades)
    return salida
