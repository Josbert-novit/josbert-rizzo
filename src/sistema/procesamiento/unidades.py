"""Resolución y normalización de unidades de presión dictadas por el operador.

En campo la presión se dice en "kilos" (kg/cm²). Los manómetros locales de cada bomba tienen
una unidad fija según el instrumento instalado (B-101 en kg/cm², B-102 en psi, B-103 en bar):
esa unidad la define el equipo, no lo que diga el operador. Un diferencial de filtro dicho
"dieciocho" en vez de "uno ocho" (1.8) también se marca para confirmar, porque está muy fuera
del rango normal de un diferencial de filtro.
"""

from sistema.procesamiento.variables import mapear_variable

_VARIABLES_DE_PRESION = {"presion_descarga", "presion_succion", "presion_diferencial"}

_FACTOR_A_KGCM2 = {
    "kg/cm2": 1.0,
    "psi": 1 / 14.2233,
    "bar": 1.019716,
}


def a_kgcm2(valor: float, unidad: str | None) -> float:
    """Convierte un valor de presión a kg/cm2 (unidad canónica del historiador). Si la unidad
    no se reconoce, devuelve el valor sin convertir."""
    if unidad is None:
        return valor
    return valor * _FACTOR_A_KGCM2.get(unidad, 1.0)


ALIAS_UNIDAD = {
    "kilo": "kg/cm2",
    "kilos": "kg/cm2",
    "kg": "kg/cm2",
    "kgcm2": "kg/cm2",
    "kg/cm2": "kg/cm2",
    "kg/cm²": "kg/cm2",
    "psi": "psi",
    "bar": "bar",
    "bares": "bar",
}

UMBRAL_DIFERENCIAL_SOSPECHOSO = 10.0


def normalizar_alias(unidad: str | None) -> str | None:
    if unidad is None:
        return None
    clave = unidad.strip().lower().replace(" ", "")
    return ALIAS_UNIDAD.get(clave, unidad)


def resolver_unidad(
    variable: str, valor: float | None, unidad: str | None, equipo: dict | None
) -> tuple[str | None, bool, str | None]:
    """Devuelve (unidad final, si hace falta confirmación del operador, motivo)."""
    unidad = normalizar_alias(unidad)
    canonica = mapear_variable(variable)

    es_presion_bomba = canonica in ("presion_descarga", "presion_succion")
    if equipo and equipo.get("tipo") == "bomba" and es_presion_bomba:
        manometro = equipo.get("manometro_local")
        if manometro:
            unidad_gauge = manometro["unidad"]
            if unidad is not None and unidad != unidad_gauge:
                return (
                    unidad_gauge,
                    True,
                    f'la unidad dicha ("{unidad}") no coincide con el manómetro local de '
                    f'{equipo["tag"]} ({unidad_gauge}): confirmar con el operador',
                )
            unidad = unidad_gauge

    if equipo and equipo.get("tipo") == "filtro" and canonica == "presion_diferencial" and valor is not None:
        if abs(valor) >= UMBRAL_DIFERENCIAL_SOSPECHOSO:
            return (
                unidad,
                True,
                f'diferencial de {valor} parece "dieciocho" dicho en vez de "uno ocho" (1.8): '
                "confirmar con el operador",
            )

    return unidad, False, None
