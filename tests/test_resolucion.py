from sistema.procesamiento.resolucion import ResolverNombres, ResultadoResolucion

EQUIPOS = {
    "equipos": [
        {"tag": "B-101", "tipo": "bomba", "nombres": ["la uno", "la 1", "la de la punta"]},
        {"tag": "B-102", "tipo": "bomba", "nombres": ["la dos", "la 2", "la del medio"]},
        {"tag": "B-103", "tipo": "bomba", "nombres": ["la tres", "la de reserva"]},
        {"tag": "F-201A", "tipo": "filtro", "nombres": ["el filtro A", "el de la izquierda"]},
        {"tag": "F-201B", "tipo": "filtro", "nombres": ["el filtro B", "el de la derecha"]},
    ]
}


class HttpFalso:
    def equipos(self):
        return EQUIPOS


def test_resuelve_por_tag_real():
    r = ResolverNombres(HttpFalso()).resolver("b-102")
    assert r.resultado == ResultadoResolucion.resuelto
    assert r.tag == "B-102"


def test_resuelve_por_nombre_informal_especifico():
    r = ResolverNombres(HttpFalso()).resolver("la dos")
    assert r.resultado == ResultadoResolucion.resuelto
    assert r.tag == "B-102"


def test_resuelve_nombre_con_acento_y_mayusculas():
    r = ResolverNombres(HttpFalso()).resolver("La De Reserva")
    assert r.resultado == ResultadoResolucion.resuelto
    assert r.tag == "B-103"


def test_termino_generico_ambiguo_entre_varias_bombas():
    r = ResolverNombres(HttpFalso()).resolver("la bomba")
    assert r.resultado == ResultadoResolucion.ambiguo
    assert set(r.candidatos) == {"B-101", "B-102", "B-103"}


def test_termino_generico_resuelto_si_hay_un_solo_candidato():
    equipos_un_tanque = {
        "equipos": [
            *EQUIPOS["equipos"],
            {"tag": "TK-301", "tipo": "tanque", "nombres": ["el pulmón"]},
        ]
    }

    class HttpUnTanque:
        def equipos(self):
            return equipos_un_tanque

    r = ResolverNombres(HttpUnTanque()).resolver("el tanque")
    assert r.resultado == ResultadoResolucion.resuelto
    assert r.tag == "TK-301"


def test_no_encontrado():
    r = ResolverNombres(HttpFalso()).resolver("el motor diesel")
    assert r.resultado == ResultadoResolucion.no_encontrado
    assert r.tag is None


def test_resuelve_nombre_envuelto_en_frase_del_operador():
    # El operador no repite el nombre tal cual: contesta con relleno y puntuación
    # (ver BUGS.md #15). El nombre específico adentro de la frase debe alcanzar.
    r = ResolverNombres(HttpFalso()).resolver("La correcta es el filtro A.")
    assert r.resultado == ResultadoResolucion.resuelto
    assert r.tag == "F-201A"


def test_nombre_especifico_le_gana_al_tipo_generico_en_la_misma_frase():
    # "filtro" (genérico, matchea F-201A y F-201B) aparece en la misma frase que "el de la
    # izquierda" (específico de F-201A): debe ganar el match más largo/específico.
    r = ResolverNombres(HttpFalso()).resolver("era el filtro, el de la izquierda")
    assert r.resultado == ResultadoResolucion.resuelto
    assert r.tag == "F-201A"
