from datetime import datetime, timezone

from planta_contrato import EstadoOrden, OrdenSistema, Repuesto

from sistema.procesamiento.reglas_aprobacion import evaluar_reglas

AHORA = datetime.now(timezone.utc)

TAREAS_ESTANDAR = [
    {"codigo": "T-B-EMP", "equipos": ["B-101"]},
    {"codigo": "T-B-VAL", "equipos": ["B-101"]},
]


class CatalogoFake:
    """Catálogo de materiales inyectable: valores elegidos a propósito para pegarle justo a los
    límites del anexo (USD 2499/2500), sin depender de los montos reales de
    `datos/materiales.yaml`."""

    def __init__(self, repuestos: dict[str, dict]):
        self._repuestos = repuestos

    def repuesto(self, codigo: str) -> dict | None:
        return self._repuestos.get(codigo)

    def tareas_estandar(self, equipo: str | None = None) -> list[dict]:
        return [t for t in TAREAS_ESTANDAR if equipo is None or equipo in t["equipos"]]


def _orden(
    tareas=("T-B-EMP",), detiene_equipo=False, ejecutor="propio", repuestos=(), equipo="B-101",
) -> OrdenSistema:
    return OrdenSistema(
        id="ORD-1", ts=AHORA, estado=EstadoOrden.borrador, equipo=equipo,
        tareas=list(tareas), detiene_equipo=detiene_equipo, reduce_inyeccion=False,
        ejecutor=ejecutor, repuestos=[Repuesto(codigo=c, cantidad=q) for c, q in repuestos],
    )


def test_aprueba_cuando_no_hay_ningun_motivo():
    catalogo = CatalogoFake({})
    orden = _orden()

    assert evaluar_reglas(orden, catalogo, disponibilidad_reserva=True) == []


def test_costo_justo_debajo_del_tope_aprueba():
    catalogo = CatalogoFake({"R-1": {"costo_usd": 2499, "stock": {"yacimiento": 5}}})
    orden = _orden(repuestos=[("R-1", 1)])

    assert evaluar_reglas(orden, catalogo, disponibilidad_reserva=True, costo_max_usd=2500) == []


def test_costo_igual_al_tope_no_aprueba():
    catalogo = CatalogoFake({"R-1": {"costo_usd": 2500, "stock": {"yacimiento": 5}}})
    orden = _orden(repuestos=[("R-1", 1)])

    motivos = evaluar_reglas(orden, catalogo, disponibilidad_reserva=True, costo_max_usd=2500)

    assert any("costo" in m for m in motivos)


def test_tx_val_usd_1900_aprueba_por_costo():
    catalogo = CatalogoFake({"TX-VAL-KIT": {"costo_usd": 1900, "stock": {"yacimiento": 1}}})
    orden = _orden(tareas=["T-B-VAL"], repuestos=[("TX-VAL-KIT", 1)])

    assert evaluar_reglas(orden, catalogo, disponibilidad_reserva=True) == []


def test_repuesto_sin_stock_en_el_almacen_nunca_aprueba():
    catalogo = CatalogoFake({"TX-ROD-CIG": {"costo_usd": 10, "stock": {"yacimiento": 0, "central": 2}}})
    orden = _orden(tareas=["NO-ESTANDAR"], repuestos=[("TX-ROD-CIG", 1)])

    motivos = evaluar_reglas(orden, catalogo, disponibilidad_reserva=True)

    assert any("stock" in m for m in motivos)


def test_detiene_equipo_sin_reserva_no_aprueba():
    catalogo = CatalogoFake({})
    orden = _orden(detiene_equipo=True)

    motivos = evaluar_reglas(orden, catalogo, disponibilidad_reserva=False)

    assert any("reserva" in m for m in motivos)


def test_detiene_equipo_con_reserva_aprueba():
    catalogo = CatalogoFake({})
    orden = _orden(detiene_equipo=True)

    assert evaluar_reglas(orden, catalogo, disponibilidad_reserva=True) == []


def test_ejecutor_contratista_no_aprueba():
    catalogo = CatalogoFake({})
    orden = _orden(ejecutor="contratista")

    motivos = evaluar_reglas(orden, catalogo, disponibilidad_reserva=True)

    assert any("ejecutor" in m for m in motivos)


def test_tarea_fuera_de_catalogo_no_aprueba():
    catalogo = CatalogoFake({})
    orden = _orden(tareas=["T-INVENTADA"])

    motivos = evaluar_reglas(orden, catalogo, disponibilidad_reserva=True)

    assert any("catálogo" in m for m in motivos)


def test_no_estandar_no_dispara_el_chequeo_de_catalogo():
    catalogo = CatalogoFake({})
    orden = _orden(tareas=["NO-ESTANDAR"])

    assert evaluar_reglas(orden, catalogo, disponibilidad_reserva=True) == []


def test_siempre_pendiente_fuerza_revision_aunque_todo_lo_demas_pase():
    catalogo = CatalogoFake({})
    orden = _orden(equipo="B-101")

    motivos = evaluar_reglas(
        orden, catalogo, disponibilidad_reserva=True, siempre_pendiente=frozenset({"B-101"}),
    )

    assert any("B-101" in m for m in motivos)


def test_danio_estructural_fuerza_revision_aunque_todo_lo_demas_pase():
    catalogo = CatalogoFake({})
    orden = _orden()

    motivos = evaluar_reglas(orden, catalogo, disponibilidad_reserva=True, danio_estructural=True)

    assert any("daño estructural" in m for m in motivos)
