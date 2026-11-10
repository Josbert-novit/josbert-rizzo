"""Servidor MCP (Fase 3.2) que expone las tools de solo lectura de `tools.py` para que un
cliente MCP (Claude Desktop, Cursor, etc.) pueda consultar la planta sin tocar nada.

Se corre por stdio (lo que espera la mayoría de los clientes MCP), sin depender de los demás
servicios de `docker-compose.yml`:

    .venv/Scripts/python -m sistema.integraciones.mcp_server

o, para probarlo desde adentro del contenedor del agente:

    docker compose exec agente python -m sistema.integraciones.mcp_server
"""

from mcp.server.mcpserver import MCPServer

from sistema.integraciones import tools
from sistema.integraciones.planta_http import PlantaHTTP

mcp = MCPServer(name="josbert-rizzo-planta", instructions="Tools de solo lectura sobre la planta de inyección PIA-3.")

_http = PlantaHTTP()


@mcp.tool()
def equipos() -> tools.RespuestaEquipos:
    """Equipos de la planta: tag, tipo, fabricante/modelo y nombres informales."""
    return tools.tool_equipos(_http)


@mcp.tool()
def historial_sensor(tag: str, variable: str, desde: str, hasta: str) -> tools.RespuestaHistorialSensor:
    """Historial de un sensor entre dos fechas ISO 8601 (una muestra por minuto, hasta 45 días)."""
    pedido = tools.PedidoHistorialSensor(tag=tag, variable=variable, desde=desde, hasta=hasta)
    return tools.tool_historial_sensor(_http, pedido)


@mcp.tool()
def historial_ordenes(
    equipo: str | None = None, desde: str | None = None, hasta: str | None = None
) -> tools.RespuestaHistorialOrdenes:
    """Órdenes de trabajo históricas de un equipo (archivo de dos años)."""
    pedido = tools.PedidoHistorialOrdenes(equipo=equipo, desde=desde, hasta=hasta)
    return tools.tool_historial_ordenes(_http, pedido)


@mcp.tool()
def inventario(equipo: str | None = None) -> tools.RespuestaInventario:
    """Repuestos y stock por almacén (yacimiento / central). Con `equipo`, solo los suyos."""
    return tools.tool_inventario(tools.PedidoInventario(equipo=equipo))


@mcp.tool()
def catalogo_tareas(equipo: str | None = None) -> tools.RespuestaCatalogoTareas:
    """Catálogo de tareas estándar y no estándar del plan de mantenimiento."""
    return tools.tool_catalogo_tareas(tools.PedidoCatalogoTareas(equipo=equipo))


@mcp.tool()
def documentos() -> tools.RespuestaDocumentos:
    """Catálogo de manuales disponibles, con cuál revisión está vigente."""
    return tools.tool_documentos(_http)


@mcp.tool()
def buscar_manuales(
    consulta: str, doc_id: str | None = None, solo_vigentes: bool = True, maximo: int = 5
) -> tools.RespuestaBuscarManuales:
    """Busca palabras clave en las secciones de los manuales. Devuelve la cita de cada
    coincidencia (p. ej. "TX350-D §3.2"). Excluye revisiones vencidas por default."""
    pedido = tools.PedidoBuscarManuales(consulta=consulta, doc_id=doc_id, solo_vigentes=solo_vigentes, maximo=maximo)
    return tools.tool_buscar_manuales(_http, pedido)


if __name__ == "__main__":
    mcp.run()
