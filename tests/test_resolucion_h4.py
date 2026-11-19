"""Hallazgo H4 (Fase 15, corrida v6 del corrector): nombres que el LLM extrae recortados o
piezas tomadas como equipos. Usa los nombres reales de `academia2026-tp/datos/planta.yaml`."""

import pytest

from sistema.procesamiento.interpretacion import _heredar_equipo_si_es_componente, es_componente
from sistema.procesamiento.resolucion import Resolucion, ResolverNombres, ResultadoResolucion

PLANTA = {
    "equipos": [
        {"tag": "B-101", "tipo": "bomba", "nombres": ["la uno", "la 1", "la de la punta", "la del portón", "bomba uno"]},
        {"tag": "B-102", "tipo": "bomba", "nombres": ["la dos", "la 2", "la del medio", "bomba dos"]},
        {"tag": "B-103", "tipo": "bomba", "nombres": ["la tres", "la 3", "la del fondo", "la de reserva", "bomba tres"]},
        {"tag": "F-201A", "tipo": "filtro", "nombres": ["el filtro A", "el primario", "el de la izquierda", "el grueso"]},
        {"tag": "F-201B", "tipo": "filtro", "nombres": ["el filtro B", "el secundario", "el de la derecha", "el fino"]},
        {"tag": "TK-301", "tipo": "tanque", "nombres": ["el pulmón", "el tanque", "el tanque pulmón"]},
        {"tag": "FT-401", "tipo": "caudalimetro", "nombres": ["el caudalímetro", "el colector"]},
    ]
}


class HttpPlanta:
    def equipos(self):
        return PLANTA


@pytest.fixture
def resolver():
    return ResolverNombres(HttpPlanta())


@pytest.mark.parametrize(
    "dicho, tag",
    [
        # Casos reales de la corrida v6 que quedaban "no encontrado".
        ("pulmón", "TK-301"),
        ("Pulmon", "TK-301"),
        ("B", "F-201B"),
        ("filtro B", "F-201B"),
        ("A", "F-201A"),
        # Recortes del mismo tipo.
        ("medio", "B-102"),
        ("del medio", "B-102"),
        ("reserva", "B-103"),
        ("izquierda", "F-201A"),
        ("tanque pulmón", "TK-301"),
        # Lo que ya andaba sigue andando.
        ("la dos", "B-102"),
        ("el pulmón", "TK-301"),
        ("la correcta es el filtro A.", "F-201A"),
        ("B-101", "B-101"),
    ],
)
def test_resuelve_nombres_recortados(resolver, dicho, tag):
    r = resolver.resolver(dicho)
    assert r.resultado == ResultadoResolucion.resuelto, (dicho, r)
    assert r.tag == tag


def test_por_palabras_y_no_por_caracteres(resolver):
    """'la 1' no tiene que matchear dentro de 'la 10' (antes era substring de caracteres)."""
    assert resolver.resolver("la 10").tag != "B-101"


def test_generico_sigue_ambiguo(resolver):
    r = resolver.resolver("la bomba")
    assert r.resultado == ResultadoResolucion.ambiguo
    assert set(r.candidatos) == {"B-101", "B-102", "B-103"}


def test_filtro_generico_sigue_ambiguo(resolver):
    r = resolver.resolver("el filtro")
    assert r.resultado == ResultadoResolucion.ambiguo
    assert set(r.candidatos) == {"F-201A", "F-201B"}


def test_rodamiento_no_es_un_equipo(resolver):
    assert resolver.resolver("rodamiento").resultado == ResultadoResolucion.no_encontrado


# --- piezas que heredan el equipo de la fila anterior --------------------------------------

@pytest.mark.parametrize("dicho", ["rodamiento", "El rodamiento", "los sellos", "empaquetadura", "aceite", "acople"])
def test_es_componente(dicho):
    assert es_componente(dicho)


@pytest.mark.parametrize("dicho", ["la dos", "el pulmón", "rodamiento de la uno", "", None])
def test_no_es_componente(dicho):
    assert not es_componente(dicho)


def test_componente_hereda_el_equipo_anterior():
    no_encontrado = Resolucion(ResultadoResolucion.no_encontrado)
    r = _heredar_equipo_si_es_componente("rodamiento", no_encontrado, "B-102")
    assert r.resultado == ResultadoResolucion.resuelto
    assert r.tag == "B-102"


def test_componente_sin_equipo_anterior_se_sigue_preguntando():
    no_encontrado = Resolucion(ResultadoResolucion.no_encontrado)
    assert _heredar_equipo_si_es_componente("rodamiento", no_encontrado, None) is no_encontrado


def test_no_componente_no_hereda():
    no_encontrado = Resolucion(ResultadoResolucion.no_encontrado)
    assert _heredar_equipo_si_es_componente("pulmoncito", no_encontrado, "B-102") is no_encontrado


def test_ambiguo_no_se_pisa_con_herencia():
    ambiguo = Resolucion(ResultadoResolucion.ambiguo, candidatos=["F-201A", "F-201B"])
    assert _heredar_equipo_si_es_componente("rodamiento", ambiguo, "B-102") is ambiguo
