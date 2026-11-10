"""Mapea la descripción libre de una variable (como la devuelve el LLM de extracción) a la
clave canónica que usan los sensores de la planta (GET /api/planta, GET /api/historian)."""

import unicodedata

_REGLAS: list[tuple[str, str]] = [
    ("diferencial", "presion_diferencial"),
    ("descarga", "presion_descarga"),
    ("succion", "presion_succion"),
    ("aspiracion", "presion_succion"),
    ("vibracion", "vibracion"),
    ("aceite", "temperatura_aceite"),
    ("carter", "temperatura_aceite"),
    ("caudal", "caudal"),
    ("nivel", "nivel"),
]


def _normalizar(texto: str) -> str:
    texto = texto.strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def mapear_variable(variable_dicha: str) -> str | None:
    """Devuelve la clave canónica (p. ej. "presion_descarga") o None si no se reconoce
    (lecturas puramente locales sin equivalente conectado, como temperatura por IR o goteo)."""
    normalizada = _normalizar(variable_dicha)
    for palabra_clave, canonica in _REGLAS:
        if palabra_clave in normalizada:
            return canonica
    return None
