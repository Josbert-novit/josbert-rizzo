import json

from fastapi import FastAPI, HTTPException
from planta_contrato import EstadoOrden, OrdenSistema
from pydantic import BaseModel

from sistema.config import config
from sistema.persistencia import ordenes as persistencia_ordenes
from sistema.procesamiento.certificacion import calcular_monto_habilita_usd

app = FastAPI(title="Central de mantenimiento")


@app.get("/api/estado")
def estado() -> dict:
    return {"estado": "ok"}


@app.get("/api/partes")
def partes() -> list[dict]:
    """Los partes que arma el agente (uno por nota), para inspección y para la evaluación de la
    Fase 4.3 (comparar contra `GET /api/verdad` de la planta)."""
    if not config.media_dir.exists():
        return []
    resultado = []
    for archivo in sorted(config.media_dir.glob("*/parte.json")):
        resultado.append(json.loads(archivo.read_text(encoding="utf-8")))
    return resultado


@app.get("/api/partes/{nota_id}")
def parte(nota_id: str) -> dict:
    archivo = config.media_dir / nota_id / "parte.json"
    if not archivo.exists():
        raise HTTPException(404, f"No hay parte para la nota {nota_id}")
    return json.loads(archivo.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- Órdenes (Fase 12)
#
# Interfaz de aprobación/cierre/certificación (3.3, el anexo acepta "CLI, API o web" — se elige
# API, curl-documentada acá abajo, sin UI gráfica). Respaldada en Postgres, no en archivos, para
# sobrevivir reinicios (F3). Cada endpoint SOLO cambia `estado` + dispara la republicación
# (`pendiente_publicar=true`, el hilo de reconciliación de `agente.py` la toma de ahí) — nunca
# re-decide `Aprobada`/`PendienteAprobacion`: esa decisión ya la tomó
# `reglas_aprobacion.evaluar_reglas` (Fase 9). Un humano solo avanza una orden `PendienteAprobacion`
# ya existente, cierra o certifica — no "arregla" una que las reglas ya rechazaron (evita abrir
# una puerta a `aprobacion_indebida`).


class RechazoBody(BaseModel):
    motivo: str


class ObservacionBody(BaseModel):
    motivo: str


class CertificacionBody(BaseModel):
    habilita: str  # número HAB-AAAA-NNNN que asigna el certificador (3.4); el monto lo calcula el sistema


def _cargar_o_404(conn, orden_id: str) -> OrdenSistema:
    orden = persistencia_ordenes.cargar(conn, orden_id)
    if orden is None:
        raise HTTPException(404, f"No hay orden {orden_id}")
    return orden


def _actualizar(orden_id: str, cambios: dict, desde: EstadoOrden | None = None) -> dict:
    conn = persistencia_ordenes.conectar()
    try:
        orden = _cargar_o_404(conn, orden_id)
        if desde is not None and orden.estado != desde:
            raise HTTPException(
                409, f"la orden {orden_id} está en estado '{orden.estado.value}', no '{desde.value}'"
            )
        actualizada = orden.model_copy(update=cambios)
        persistencia_ordenes.guardar(conn, actualizada, pendiente_publicar=True)
    finally:
        conn.close()
    return actualizada.model_dump(mode="json")


@app.get("/api/ordenes")
def listar_ordenes() -> list[dict]:
    conn = persistencia_ordenes.conectar()
    try:
        return [o.model_dump(mode="json") for o in persistencia_ordenes.cargar_todas(conn)]
    finally:
        conn.close()


@app.get("/api/ordenes/pendientes")
def listar_ordenes_pendientes() -> list[dict]:
    """Para el planificador: lo que necesita revisar a mano porque las reglas (Fase 9) no
    pudieron aprobarlo solas."""
    conn = persistencia_ordenes.conectar()
    try:
        todas = persistencia_ordenes.cargar_todas(conn)
    finally:
        conn.close()
    return [o.model_dump(mode="json") for o in todas if o.estado == EstadoOrden.pendiente]


@app.get("/api/ordenes/{orden_id}")
def obtener_orden(orden_id: str) -> dict:
    conn = persistencia_ordenes.conectar()
    try:
        return _cargar_o_404(conn, orden_id).model_dump(mode="json")
    finally:
        conn.close()


@app.get("/api/ordenes/{orden_id}/sugerencia")
def obtener_sugerencia(orden_id: str) -> dict:
    """Lo que propuso el análisis con LLM para esta orden (causa probable → tareas y repuestos).
    Solo informativo para el planificador: la orden publicada no cambia (ver
    `procesamiento/ordenes.py::orden_inmediata`)."""
    conn = persistencia_ordenes.conectar()
    try:
        _cargar_o_404(conn, orden_id)
        sugerencia = persistencia_ordenes.cargar_sugerencia(conn, orden_id)
        if sugerencia is None:
            raise HTTPException(404, f"Todavía no hay sugerencia del análisis para {orden_id}")
        return sugerencia.model_dump(mode="json")
    finally:
        conn.close()


@app.post("/api/ordenes/{orden_id}/aprobar")
def aprobar_orden(orden_id: str) -> dict:
    """`curl -X POST http://localhost:8200/api/ordenes/ORD-1/aprobar`"""
    return _actualizar(orden_id, {"estado": EstadoOrden.aprobada, "motivo": None}, desde=EstadoOrden.pendiente)


@app.post("/api/ordenes/{orden_id}/rechazar")
def rechazar_orden(orden_id: str, body: RechazoBody) -> dict:
    """`curl -X POST .../rechazar -d '{"motivo": "no corresponde"}'`"""
    return _actualizar(
        orden_id, {"estado": EstadoOrden.rechazada, "motivo": body.motivo}, desde=EstadoOrden.pendiente
    )


@app.post("/api/ordenes/{orden_id}/cerrar")
def cerrar_orden(orden_id: str) -> dict:
    """Cierre de trabajo propio (G1, supervisor) — el cierre automático por voz
    (`planta/cierres`, Fase 14.1) ya lo lleva a `Ejecutada`; esto es para cerrarlo a mano desde la
    central. Solo para `ejecutor == "propio"` (14.3): el trabajo de contratista se certifica u
    observa, nunca se "cierra" directo."""
    conn = persistencia_ordenes.conectar()
    try:
        orden = _cargar_o_404(conn, orden_id)
    finally:
        conn.close()
    if orden.ejecutor != "propio":
        raise HTTPException(409, f"la orden {orden_id} es de '{orden.ejecutor}', no de trabajo propio — usar /certificar")
    return _actualizar(orden_id, {"estado": EstadoOrden.cerrada}, desde=EstadoOrden.ejecutada)


@app.post("/api/ordenes/{orden_id}/certificar")
def certificar_orden(orden_id: str, body: CertificacionBody) -> dict:
    """Certificación de trabajo de contratista (G, inspector; 14.3/14.4). `monto_habilita_usd`
    lo calcula el sistema contra la lista de precios vigente a la fecha de la orden
    (`procesamiento/certificacion.py`) — NUNCA lo que declare el cierre (contrato, 3.2)."""
    conn = persistencia_ordenes.conectar()
    try:
        orden = _cargar_o_404(conn, orden_id)
    finally:
        conn.close()
    if orden.ejecutor == "propio":
        raise HTTPException(409, f"la orden {orden_id} es de trabajo propio — usar /cerrar")
    monto = calcular_monto_habilita_usd(orden)
    return _actualizar(
        orden_id,
        {"estado": EstadoOrden.certificada, "habilita": body.habilita, "monto_habilita_usd": monto},
        desde=EstadoOrden.ejecutada,
    )


@app.post("/api/ordenes/{orden_id}/observar")
def observar_orden(orden_id: str, body: ObservacionBody) -> dict:
    """El trabajo de contratista no cumple lo pedido (3.3): no se certifica, queda observado con
    motivo — la contratista tiene 5 días hábiles para responder."""
    conn = persistencia_ordenes.conectar()
    try:
        orden = _cargar_o_404(conn, orden_id)
    finally:
        conn.close()
    if orden.ejecutor == "propio":
        raise HTTPException(409, f"la orden {orden_id} es de trabajo propio — usar /cerrar")
    return _actualizar(
        orden_id, {"estado": EstadoOrden.observada, "motivo": body.motivo}, desde=EstadoOrden.ejecutada
    )
