"""Tools de solo lectura (Fase 3): equipos, historial de sensores, historial de órdenes,
inventario/stock, catálogo de tareas y búsqueda en manuales. Todas reciben y devuelven modelos
Pydantic estrictos (`extra="forbid"`) y no cambian nada: ni en la planta ni en nuestro sistema.

Cada una delega el acceso de red en `PlantaHTTP`, que ya maneja timeouts y reintentos
(`src/sistema/integraciones/planta_http.py`) — acá solo se atrapa `PlantaHTTPError` para
devolverlo como parte de la respuesta en vez de reventar, así quien use la tool (el agente o un
cliente MCP) siempre recibe una respuesta tipada, nunca una excepción.
"""

import unicodedata
from functools import lru_cache

import httpx
from pydantic import BaseModel, ConfigDict, Field

from sistema.integraciones import materiales as materiales_datos
from sistema.integraciones.planta_http import PlantaHTTP, PlantaHTTPError


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorTool(_Modelo):
    mensaje: str


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_tildes.lower()


# --------------------------------------------------------------------------- equipos

class Equipo(_Modelo):
    model_config = ConfigDict(extra="allow")  # cada tipo de equipo trae campos propios (caudal_nominal_m3d, cartuchos, altura_m, ...)
    tag: str
    tipo: str
    descripcion: str | None = None
    fabricante: str | None = None
    modelo: str | None = None
    nombres: list[str] = Field(default_factory=list)
    rol_inicial: str | None = None


class RespuestaEquipos(_Modelo):
    equipos: list[Equipo] = Field(default_factory=list)
    error: ErrorTool | None = None


def tool_equipos(http: PlantaHTTP) -> RespuestaEquipos:
    """Equipos de la planta: tag, tipo, fabricante/modelo y nombres informales (`GET /api/planta`)."""
    try:
        datos = http.equipos()
    except PlantaHTTPError as e:
        return RespuestaEquipos(error=ErrorTool(mensaje=str(e)))
    return RespuestaEquipos(equipos=[Equipo.model_validate(e) for e in datos["equipos"]])


# --------------------------------------------------------------------------- historial de sensores

class MuestraSensor(_Modelo):
    ts: str
    valor: float | None = None


class PedidoHistorialSensor(_Modelo):
    tag: str
    variable: str
    desde: str
    hasta: str


class RespuestaHistorialSensor(_Modelo):
    tag: str
    variable: str
    muestras: list[MuestraSensor] = Field(default_factory=list)
    error: ErrorTool | None = None


def tool_historial_sensor(http: PlantaHTTP, pedido: PedidoHistorialSensor) -> RespuestaHistorialSensor:
    """Historial de un sensor entre dos fechas (una muestra por minuto, hasta 45 días de atrás)."""
    try:
        muestras = http.historial(pedido.tag, pedido.variable, pedido.desde, pedido.hasta)
    except PlantaHTTPError as e:
        return RespuestaHistorialSensor(tag=pedido.tag, variable=pedido.variable, error=ErrorTool(mensaje=str(e)))
    return RespuestaHistorialSensor(
        tag=pedido.tag,
        variable=pedido.variable,
        muestras=[MuestraSensor.model_validate(m) for m in muestras],
    )


# --------------------------------------------------------------------------- historial de órdenes

class RepuestoUsado(_Modelo):
    codigo: str
    cantidad: float


class OrdenHistorica(_Modelo):
    model_config = ConfigDict(extra="allow")  # el archivo (texto libre de campo) y las órdenes en vivo del simulador no comparten exactamente el mismo esquema
    id: str | None = None
    equipo: str
    fecha: str | None = None
    fecha_aviso: str | None = None
    fecha_inicio: str | None = None
    fecha_cierre: str | None = None
    estado: str | None = None
    tipo: str | None = None
    origen: str | None = None
    aviso: str | None = None
    solicitante: str | None = None
    tecnico: str | None = None
    ejecutor: str | None = None
    horas: float | None = None
    tareas: list[str] = Field(default_factory=list)
    repuestos: list[RepuestoUsado] = Field(default_factory=list)
    trabajo_realizado: str | None = None
    codigo_falla: str | None = None


class PedidoHistorialOrdenes(_Modelo):
    equipo: str | None = None
    desde: str | None = None
    hasta: str | None = None


class RespuestaHistorialOrdenes(_Modelo):
    ordenes: list[OrdenHistorica] = Field(default_factory=list)
    error: ErrorTool | None = None


def tool_historial_ordenes(http: PlantaHTTP, pedido: PedidoHistorialOrdenes) -> RespuestaHistorialOrdenes:
    """Órdenes de trabajo históricas: dos años de archivo (texto libre de campo) más el
    precalentamiento del escenario (`GET /api/historial-ordenes`)."""
    try:
        ordenes = http.historial_ordenes(pedido.equipo, pedido.desde, pedido.hasta)
    except PlantaHTTPError as e:
        return RespuestaHistorialOrdenes(error=ErrorTool(mensaje=str(e)))
    return RespuestaHistorialOrdenes(ordenes=[OrdenHistorica.model_validate(o) for o in ordenes])


# --------------------------------------------------------------------------- inventario / stock

class Repuesto(_Modelo):
    codigo: str
    nombre: str
    unidad: str
    costo_usd: float
    stock: dict[str, int]


class PedidoInventario(_Modelo):
    equipo: str | None = None


class RespuestaInventario(_Modelo):
    repuestos: list[Repuesto] = Field(default_factory=list)


def tool_inventario(pedido: PedidoInventario) -> RespuestaInventario:
    """Repuestos y stock por almacén. Con `equipo`, solo los de su lista de materiales."""
    if pedido.equipo is None:
        codigos = None
    else:
        codigos = set(materiales_datos.lista_materiales(pedido.equipo))
    repuestos = [
        Repuesto.model_validate(r)
        for r in materiales_datos.repuestos()
        if codigos is None or r["codigo"] in codigos
    ]
    return RespuestaInventario(repuestos=repuestos)


# --------------------------------------------------------------------------- catálogo de tareas

class RepuestoDeTarea(_Modelo):
    codigo: str
    cantidad: float


class TareaEstandar(_Modelo):
    codigo: str
    nombre: str
    equipos: list[str] = Field(default_factory=list)
    detiene: bool
    horas: float
    ejecutor: str
    repuestos: list[RepuestoDeTarea] = Field(default_factory=list)


class TareaNoEstandar(_Modelo):
    codigo: str
    nombre: str
    detiene: bool
    horas: float
    ejecutor: str


class PedidoCatalogoTareas(_Modelo):
    equipo: str | None = None


class RespuestaCatalogoTareas(_Modelo):
    estandar: list[TareaEstandar] = Field(default_factory=list)
    no_estandar: list[TareaNoEstandar] = Field(default_factory=list)


def tool_catalogo_tareas(pedido: PedidoCatalogoTareas) -> RespuestaCatalogoTareas:
    """Catálogo de tareas del plan de mantenimiento: estándar (con equipos y repuestos
    asociados) y no estándar (siempre requieren aprobación del planificador)."""
    return RespuestaCatalogoTareas(
        estandar=[TareaEstandar.model_validate(t) for t in materiales_datos.tareas_estandar(pedido.equipo)],
        no_estandar=[TareaNoEstandar.model_validate(t) for t in materiales_datos.tareas_no_estandar()],
    )


# --------------------------------------------------------------------------- búsqueda en manuales

class Documento(_Modelo):
    model_config = ConfigDict(extra="allow")  # catálogo heterogéneo: manuales, contrato, certificado, e índice de historial
    id: str
    titulo: str
    subtitulo: str | None = None
    codigo: str | None = None
    revision: str | None = None
    vigente_desde: str | None = None
    vigente: bool = True
    reemplaza: str | None = None
    reemplazado_por: str | None = None
    tipo: str | None = None
    emisor: str | None = None
    equipos: list[str] = Field(default_factory=list)
    archivos: dict[str, str] = Field(default_factory=dict)
    paginas: int | None = None


class RespuestaDocumentos(_Modelo):
    documentos: list[Documento] = Field(default_factory=list)
    error: ErrorTool | None = None


def tool_documentos(http: PlantaHTTP) -> RespuestaDocumentos:
    """Catálogo de manuales disponibles (`GET /api/documentos`)."""
    try:
        datos = http.documentos()
    except PlantaHTTPError as e:
        return RespuestaDocumentos(error=ErrorTool(mensaje=str(e)))
    return RespuestaDocumentos(documentos=[Documento.model_validate(d) for d in datos["documentos"]])


class Coincidencia(_Modelo):
    id: str
    doc: str
    numero: str
    titulo: str
    texto: str
    vigente: bool


class PedidoBuscarManuales(_Modelo):
    consulta: str
    doc_id: str | None = None
    solo_vigentes: bool = True
    maximo: int = 5


class RespuestaBuscarManuales(_Modelo):
    coincidencias: list[Coincidencia] = Field(default_factory=list)
    error: ErrorTool | None = None


@lru_cache(maxsize=32)
def _secciones_cacheadas(http: PlantaHTTP, doc_id: str) -> tuple[dict, ...]:
    return tuple(http.secciones(doc_id))


def tool_buscar_manuales(http: PlantaHTTP, pedido: PedidoBuscarManuales) -> RespuestaBuscarManuales:
    """Busca por palabras clave en el texto de las secciones de los manuales, con su cita
    (`"TX350-D §3.2"`). Por default excluye revisiones no vigentes (`vigente: false`) — la Rev. C
    de la bomba está vencida y no debería ganarle a la Rev. D ni en una búsqueda puntual como
    esta.

    Esto NO es el RAG semántico de la Fase 5 (ese indexa en pgvector con embeddings): es un
    matcheo de palabras simple, determinístico y de solo lectura, suficiente para una consulta
    puntual."""
    try:
        if pedido.doc_id:
            doc_ids = [pedido.doc_id]
        else:
            doc_ids = [d["id"] for d in http.documentos()["documentos"]]
    except PlantaHTTPError as e:
        return RespuestaBuscarManuales(error=ErrorTool(mensaje=str(e)))

    palabras = [p for p in _normalizar(pedido.consulta).split() if p]
    coincidencias: list[Coincidencia] = []
    for doc_id in doc_ids:
        try:
            secciones = _secciones_cacheadas(http, doc_id)
        except (PlantaHTTPError, httpx.HTTPStatusError):
            # doc_id inexistente u otro error puntual: lo salteamos, no aborta toda la búsqueda.
            continue
        for s in secciones:
            if pedido.solo_vigentes and not s.get("vigente", True):
                continue
            if all(p in _normalizar(s["texto"]) for p in palabras):
                coincidencias.append(
                    Coincidencia(
                        id=s["id"], doc=s["doc"], numero=s["numero"], titulo=s["titulo"],
                        texto=s["texto"], vigente=s.get("vigente", True),
                    )
                )
                if len(coincidencias) >= pedido.maximo:
                    return RespuestaBuscarManuales(coincidencias=coincidencias)
    return RespuestaBuscarManuales(coincidencias=coincidencias)
