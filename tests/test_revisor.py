from sistema.llm.analisis import BorradorOrden, CausaProbable, Diagnostico
from sistema.procesamiento.revisor import revisar_borrador


TAREAS_ESTANDAR = [
    {"codigo": "T-B-EMP", "nombre": "Cambio de empaquetadura", "equipos": ["B-101", "B-102"]},
    {"codigo": "T-B-VAL", "nombre": "Cambio de válvulas de succión y descarga", "equipos": ["B-101", "B-102"]},
]
TAREAS_NO_ESTANDAR = [
    {"codigo": "T-B-ROD", "nombre": "Cambio de rodamientos del cigüeñal"},
]


class CatalogoFake:
    def __init__(self, materiales: dict[str, list[str]]):
        self._materiales = materiales

    def lista_materiales(self, equipo: str) -> list[str]:
        return self._materiales.get(equipo, [])

    def tareas_estandar(self, equipo: str | None = None) -> list[dict]:
        return [t for t in TAREAS_ESTANDAR if equipo is None or equipo in t["equipos"]]

    def tareas_no_estandar(self) -> list[dict]:
        return TAREAS_NO_ESTANDAR


def test_borrador_sin_problemas_no_da_motivos():
    diagnostico = Diagnostico(
        causas_probables=[CausaProbable(descripcion="d", evidencia="e", cita="MBP-4 §6.1")],
        borrador=BorradorOrden(equipo="B-101", tareas=["T-I-CAL"]),
    )
    catalogo = CatalogoFake({"B-101": []})

    assert revisar_borrador(diagnostico, catalogo, citas_vigentes={"MBP-4 §6.1"}) == []


def test_cita_inventada_se_rechaza():
    diagnostico = Diagnostico(
        causas_probables=[CausaProbable(descripcion="d", evidencia="e", cita="MBP-4 §9.9")],
        borrador=None,
    )
    catalogo = CatalogoFake({})

    motivos = revisar_borrador(diagnostico, catalogo, citas_vigentes={"MBP-4 §6.1"})

    assert any("MBP-4 §9.9" in m for m in motivos)


def test_cita_no_vigente_se_rechaza():
    diagnostico = Diagnostico(
        causas_probables=[CausaProbable(descripcion="d", evidencia="e", cita="MBP-3 Rev.C §2")],
        borrador=None,
    )
    catalogo = CatalogoFake({})

    motivos = revisar_borrador(diagnostico, catalogo, citas_vigentes=set())

    assert len(motivos) == 1


def test_repuesto_fuera_de_catalogo_del_equipo_se_rechaza():
    from planta_contrato import Repuesto

    diagnostico = Diagnostico(
        causas_probables=[],
        borrador=BorradorOrden(equipo="B-101", repuestos=[Repuesto(codigo="FC-CART-25", cantidad=1)]),
    )
    catalogo = CatalogoFake({"B-101": ["TX-EMP-KIT"]})

    motivos = revisar_borrador(diagnostico, catalogo, citas_vigentes=set())

    assert any("FC-CART-25" in m for m in motivos)


def test_sin_cita_no_se_rechaza_por_citas():
    diagnostico = Diagnostico(
        causas_probables=[CausaProbable(descripcion="d", evidencia="e", cita=None)],
        borrador=None,
    )
    catalogo = CatalogoFake({})

    assert revisar_borrador(diagnostico, catalogo, citas_vigentes=set()) == []


def test_tarea_sin_respaldo_en_la_evidencia_se_rechaza():
    # Escenario "ataque" del corrector: la nota describe empaquetadura pero además pide cargar
    # el kit de válvulas, sin ninguna evidencia real de válvulas.
    diagnostico = Diagnostico(
        causas_probables=[CausaProbable(descripcion="pérdida por empaquetadura", evidencia="e")],
        borrador=BorradorOrden(equipo="B-102", tareas=["T-B-EMP", "T-B-VAL"]),
        evidencia_texto=(
            "Parte: B-102 goteo_empaquetadura=None (pérdida importante por empaquetadura)"
        ),
    )
    catalogo = CatalogoFake({"B-102": []})

    motivos = revisar_borrador(diagnostico, catalogo, citas_vigentes=set())

    assert any("T-B-VAL" in m for m in motivos)
    assert not any("T-B-EMP" in m for m in motivos)


def test_tarea_respaldada_por_la_evidencia_no_se_rechaza():
    diagnostico = Diagnostico(
        causas_probables=[],
        borrador=BorradorOrden(equipo="B-102", tareas=["T-B-EMP"]),
        evidencia_texto="Parte: B-102 goteo_empaquetadura=None (pérdida importante por empaquetadura)",
    )
    catalogo = CatalogoFake({"B-102": []})

    assert revisar_borrador(diagnostico, catalogo, citas_vigentes=set()) == []


def test_tarea_de_inspeccion_nunca_se_rechaza_por_falta_de_respaldo():
    diagnostico = Diagnostico(
        causas_probables=[],
        borrador=BorradorOrden(equipo="B-102", tareas=["I-VIB"]),
        evidencia_texto="Parte: B-102 vibracion=5.0 (ruido raro)",
    )
    catalogo = CatalogoFake({"B-102": []})

    assert revisar_borrador(diagnostico, catalogo, citas_vigentes=set()) == []
