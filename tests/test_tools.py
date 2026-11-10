import httpx
import pytest
from pydantic import ValidationError

from sistema.integraciones import tools
from sistema.integraciones.planta_http import PlantaHTTPError

EQUIPOS = [
    {"tag": "B-101", "tipo": "bomba", "descripcion": "Bomba 1", "fabricante": "Trimax",
     "modelo": "TX-350", "nombres": ["la uno"], "rol_inicial": "servicio", "caudal_nominal_m3d": 1300},
]

DOCUMENTOS = [
    {"id": "TX350-D", "titulo": "Manual TX-350", "revision": "D", "vigente": True, "equipos": ["B-101"]},
    {"id": "TX350-C", "titulo": "Manual TX-350", "revision": "C", "vigente": False, "equipos": ["B-101"]},
]

SECCIONES_D = [
    {"id": "TX350-D §3.2", "doc": "TX350-D", "numero": "3.2", "titulo": "Límites de presión",
     "texto": "La presión de descarga máxima es 125 kg/cm2.", "vigente": True},
]

SECCIONES_C = [
    {"id": "TX350-C §3.2", "doc": "TX350-C", "numero": "3.2", "titulo": "Límites de presión (vencido)",
     "texto": "La presión de descarga máxima es 140 kg/cm2.", "vigente": False},
]


class HttpFalso:
    def __init__(self, falla: Exception | None = None) -> None:
        self.falla = falla

    def _o_falla(self, valor):
        if self.falla:
            raise self.falla
        return valor

    def equipos(self):
        return self._o_falla({"equipos": EQUIPOS})

    def historial(self, tag, variable, desde, hasta):
        return self._o_falla([{"ts": "2026-01-01T00:00:00", "valor": 112.0}])

    def historial_ordenes(self, equipo, desde, hasta):
        return self._o_falla([
            {"id": "OT-1", "equipo": "B-101", "estado": "Cerrada", "tareas": ["T-B-VAL"],
             "repuestos": [{"codigo": "TX-VAL-KIT", "cantidad": 1}]}
        ])

    def documentos(self):
        return self._o_falla({"documentos": DOCUMENTOS})

    def secciones(self, doc_id, numero=None):
        if self.falla:
            raise self.falla
        if doc_id == "TX350-D":
            return SECCIONES_D
        if doc_id == "TX350-C":
            return SECCIONES_C
        raise httpx.HTTPStatusError("404", request=None, response=None)


# --------------------------------------------------------------------------- equipos

def test_tool_equipos_ok():
    r = tools.tool_equipos(HttpFalso())
    assert r.error is None
    assert r.equipos[0].tag == "B-101"


def test_tool_equipos_error_queda_en_la_respuesta_no_explota():
    r = tools.tool_equipos(HttpFalso(falla=PlantaHTTPError("caído")))
    assert r.error is not None
    assert r.equipos == []


# --------------------------------------------------------------------------- historial de sensores

def test_tool_historial_sensor_ok():
    pedido = tools.PedidoHistorialSensor(tag="B-101", variable="presion_descarga", desde="d", hasta="h")
    r = tools.tool_historial_sensor(HttpFalso(), pedido)
    assert len(r.muestras) == 1
    assert r.muestras[0].valor == 112.0


def test_pedido_historial_sensor_estricto_rechaza_campo_extra():
    with pytest.raises(ValidationError):
        tools.PedidoHistorialSensor(tag="B-101", variable="x", desde="d", hasta="h", otro="no")


# --------------------------------------------------------------------------- historial de órdenes

def test_tool_historial_ordenes_ok():
    pedido = tools.PedidoHistorialOrdenes(equipo="B-101")
    r = tools.tool_historial_ordenes(HttpFalso(), pedido)
    assert r.ordenes[0].id == "OT-1"
    assert r.ordenes[0].repuestos[0].codigo == "TX-VAL-KIT"


# --------------------------------------------------------------------------- inventario

def test_tool_inventario_sin_filtro_trae_todo():
    r = tools.tool_inventario(tools.PedidoInventario())
    assert len(r.repuestos) > 5


def test_tool_inventario_filtra_por_equipo():
    r = tools.tool_inventario(tools.PedidoInventario(equipo="F-201A"))
    codigos = {rep.codigo for rep in r.repuestos}
    assert "FC-CART-25" in codigos
    assert "TX-EMP-KIT" not in codigos


# --------------------------------------------------------------------------- catálogo de tareas

def test_tool_catalogo_tareas_filtra_por_equipo():
    r = tools.tool_catalogo_tareas(tools.PedidoCatalogoTareas(equipo="F-201A"))
    assert all("F-201A" in t.equipos for t in r.estandar)
    assert len(r.no_estandar) > 0


# --------------------------------------------------------------------------- manuales

def test_tool_documentos_ok():
    r = tools.tool_documentos(HttpFalso())
    assert {d.id for d in r.documentos} == {"TX350-D", "TX350-C"}


def test_buscar_manuales_excluye_no_vigentes_por_default():
    pedido = tools.PedidoBuscarManuales(consulta="presion descarga maxima")
    r = tools.tool_buscar_manuales(HttpFalso(), pedido)
    assert len(r.coincidencias) == 1
    assert r.coincidencias[0].doc == "TX350-D"


def test_buscar_manuales_puede_incluir_no_vigentes():
    pedido = tools.PedidoBuscarManuales(consulta="presion descarga maxima", solo_vigentes=False)
    r = tools.tool_buscar_manuales(HttpFalso(), pedido)
    docs = {c.doc for c in r.coincidencias}
    assert docs == {"TX350-D", "TX350-C"}


def test_buscar_manuales_ignora_tildes_y_mayusculas():
    pedido = tools.PedidoBuscarManuales(consulta="PRESIÓN DESCARGA MÁXIMA")
    r = tools.tool_buscar_manuales(HttpFalso(), pedido)
    assert len(r.coincidencias) == 1


def test_buscar_manuales_sin_coincidencias():
    pedido = tools.PedidoBuscarManuales(consulta="palabras que no están en ningún lado")
    r = tools.tool_buscar_manuales(HttpFalso(), pedido)
    assert r.coincidencias == []
    assert r.error is None
