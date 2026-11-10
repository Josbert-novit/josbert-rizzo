from sistema.rag.fallas import TraductorFallas, _parsear_tabla

TABLA = """6.1 Resumen del catálogo
Código | Falla | Fabricante | Tarea | Ejecutor
F01 | Pérdida por empaquetadura | TX-T01 | T-B-EMP | propio
F07 | Cartuchos colmatados | FC-T01 | T-F-CAR | propio
F10 | Sensor congelado o con deriva | TX-T08 / FC-T04 | T-I-CAL | propio"""


def test_parsear_tabla_un_codigo_por_fila():
    tabla = _parsear_tabla(TABLA)
    assert tabla["F01"] == ["TX-T01"]
    assert tabla["F07"] == ["FC-T01"]


def test_parsear_tabla_dos_codigos_separados_por_barra():
    tabla = _parsear_tabla(TABLA)
    assert tabla["F10"] == ["TX-T08", "FC-T04"]


class HttpFalso:
    def secciones(self, doc_id, numero=None):
        assert doc_id == "MBP-4" and numero == "6.1"
        return [{"texto": TABLA}]


def test_traductor_a_codigo_fabricante():
    t = TraductorFallas(HttpFalso())
    assert t.a_codigo_fabricante("F01") == ["TX-T01"]
    assert t.a_codigo_fabricante("f01") == ["TX-T01"]  # no distingue mayúsculas


def test_traductor_a_codigo_planta():
    t = TraductorFallas(HttpFalso())
    assert t.a_codigo_planta("FC-T01") == "F07"
    assert t.a_codigo_planta("TX-T08") == "F10"
    assert t.a_codigo_planta("FC-T04") == "F10"


def test_traductor_codigo_desconocido():
    t = TraductorFallas(HttpFalso())
    assert t.a_codigo_fabricante("F99") == []
    assert t.a_codigo_planta("NO-EXISTE") is None
