"""Reglas de aprobación (D, Fase 9): la ÚNICA función que decide si una orden queda `Aprobada`
o `PendienteAprobacion`. Ni el análisis LLM (`llm/analisis.py`) ni el revisor
(`procesamiento/revisor.py`) tocan el estado final — así un prompt manipulado (la eliminatoria
`inyeccion_de_instrucciones`) no puede aprobar nada por su cuenta, y un caso que debería quedar
pendiente no se escapa por un error de redacción del LLM (`aprobacion_indebida`, eliminatoria).

Es una función pura: recibe el borrador y todo lo que necesita evaluar ya calculado (catálogo de
materiales, si hay reserva, si hubo daño estructural) y devuelve los motivos por los que NO se
puede aprobar sola (lista vacía = aprueba). El caller (`procesamiento/ordenes.py`, Fase 8) decide
qué hacer con esos motivos (armar `OrdenSistema.motivo`, publicar `PendienteAprobacion`).
"""

from __future__ import annotations

from typing import Protocol

from planta_contrato import OrdenSistema


class CatalogoMateriales(Protocol):
    """Lo mínimo que `evaluar_reglas` necesita de `integraciones/materiales.py` — como
    parámetro en vez de importarlo directo, para poder testear casos límite (USD 2499/2500,
    stock 0) sin depender de los valores reales de `datos/materiales.yaml`."""

    def repuesto(self, codigo: str) -> dict | None: ...
    def tareas_estandar(self, equipo: str | None = None) -> list[dict]: ...


def hay_reserva_disponible(equipo: str, http) -> bool:
    """Regla 2 del anexo (continuidad de inyección): hay reserva si algún otro equipo del mismo
    grupo (mismo prefijo, p. ej. B-101/B-102/B-103) está `parada` — disponible para arrancar en
    su lugar. Agrupa por prefijo (todo antes del último guion): alcanza para las bombas B-10x del
    anexo; si otro grupo de equipos no sigue esa convención, ver `academia2026-tp/planta/sim/
    planta_sim/modelo.py` (campo `Bomba.estado`: `marcha | parada | mantenimiento | disparo`).

    Si el equipo NO está en marcha (ya parado, disparado o en mantenimiento, o no es una bomba),
    detenerlo para el trabajo no le saca nada a la inyección: la regla se cumple sin reserva. Es
    el mismo criterio que usa el corrector (`puntuar.py::reglas`)."""
    estado = http.estado()
    estados = {b["tag"]: b["estado"] for b in estado.get("bombas", [])}
    if estados.get(equipo) != "marcha":
        return True
    grupo = equipo.rsplit("-", 1)[0]
    return any(
        tag != equipo and tag.rsplit("-", 1)[0] == grupo and e == "parada"
        for tag, e in estados.items()
    )


def evaluar_reglas(
    borrador: OrdenSistema,
    catalogo: CatalogoMateriales,
    disponibilidad_reserva: bool,
    danio_estructural: bool = False,
    *,
    costo_max_usd: float = 2500,
    almacen: str = "yacimiento",
    siempre_pendiente: frozenset[str] = frozenset(),
) -> list[str]:
    motivos: list[str] = []

    # 1. Tarea estándar: cada tarea tiene que estar en el catálogo de tareas estándar DEL EQUIPO
    # (BUGS #77). 'NO-ESTANDAR' es, por definición, no estándar: va a revisión humana. Una orden
    # sin tareas tampoco se aprueba sola. Mismo criterio que el corrector (`puntuar.py::reglas`).
    codigos_estandar = {t["codigo"] for t in catalogo.tareas_estandar(borrador.equipo)}
    if not borrador.tareas:
        motivos.append("la orden no tiene tareas")
    for codigo in borrador.tareas:
        if codigo == "NO-ESTANDAR":
            motivos.append("tarea no estándar (NO-ESTANDAR): requiere aprobación humana")
        elif codigo not in codigos_estandar:
            motivos.append(
                f"tarea '{codigo}' no está en el catálogo de tareas estándar de '{borrador.equipo}'"
            )

    # 2. Continuidad de inyección: si la tarea detiene el equipo, tiene que haber reserva.
    if borrador.detiene_equipo and not disponibilidad_reserva:
        motivos.append(f"detiene '{borrador.equipo}' y no hay equipo de reserva disponible")

    # 3. Repuestos: tienen que estar en catálogo y alcanzar el stock del almacén de la planta.
    for repuesto in borrador.repuestos:
        info = catalogo.repuesto(repuesto.codigo)
        if info is None:
            motivos.append(f"repuesto '{repuesto.codigo}' no está en el catálogo")
            continue
        stock = info["stock"].get(almacen, 0)
        if repuesto.cantidad > stock:
            motivos.append(
                f"repuesto '{repuesto.codigo}': pide {repuesto.cantidad}, "
                f"stock en '{almacen}' es {stock}"
            )

    # 4. Costo: estrictamente menor al tope (el límite exacto queda PENDIENTE, no aprobado).
    costo_total = 0.0
    for repuesto in borrador.repuestos:
        info = catalogo.repuesto(repuesto.codigo)
        if info is not None:
            costo_total += info["costo_usd"] * repuesto.cantidad
    if costo_total >= costo_max_usd:
        motivos.append(f"costo total USD {costo_total:.2f} >= tope USD {costo_max_usd:.2f}")

    # 5. Ejecutor: solo se auto-aprueba el trabajo propio.
    if borrador.ejecutor != "propio":
        motivos.append(f"ejecutor '{borrador.ejecutor}' no es 'propio'")

    # 6. Overrides: siempre a revisión humana, sin importar que las otras 5 reglas pasen.
    if borrador.equipo in siempre_pendiente:
        motivos.append(f"'{borrador.equipo}' siempre requiere aprobación humana")
    if danio_estructural:
        motivos.append("daño estructural detectado: requiere aprobación humana")

    return motivos
