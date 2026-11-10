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
