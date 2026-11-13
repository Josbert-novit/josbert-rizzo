from sistema.rag.indexar import _equipos_de_fragmento

EQUIPOS_POR_DOC = {"TX350-D": ["B-101", "B-102", "B-103"], "MBP-4": ["B-101", "F-201A"]}


def test_equipos_de_fragmento_de_manual_usa_el_doc_padre():
    frag = {"doc": "TX350-D", "titulo": "Límites de operación"}
    assert _equipos_de_fragmento(frag, EQUIPOS_POR_DOC) == ["B-101", "B-102", "B-103"]


def test_equipos_de_fragmento_de_historial_se_saca_del_titulo():
    frag = {"doc": "historial", "titulo": "OT-24-0001 B-101 T-B-VAL"}
    assert _equipos_de_fragmento(frag, EQUIPOS_POR_DOC) == ["B-101"]


def test_equipos_de_fragmento_de_historial_sin_titulo_parseable():
    frag = {"doc": "historial", "titulo": None}
    assert _equipos_de_fragmento(frag, EQUIPOS_POR_DOC) == []


def test_equipos_de_fragmento_doc_sin_equipos_devuelve_vacio():
    frag = {"doc": "CT-SVP-2025", "titulo": "x"}
    assert _equipos_de_fragmento(frag, EQUIPOS_POR_DOC) == []


def test_secciones_de_otros_modelos_o_plantas_no_aplican():
    from sistema.rag.indexar import _aplica_a_esta_planta

    for titulo in [
        "Límites de operación de otros modelos de la familia",
        "Límites de otras plantas",
        "PIA-1: bombas centrífugas multietapa",
        "PIA-2: inyección de polímero",
    ]:
        assert not _aplica_a_esta_planta({"titulo": titulo}), titulo


def test_secciones_propias_aplican():
    from sistema.rag.indexar import _aplica_a_esta_planta

    for titulo in ["Límites de operación", "La familia TX", "Alcance de este manual", "PIA-3: inyección", None]:
        assert _aplica_a_esta_planta({"titulo": titulo}), titulo
