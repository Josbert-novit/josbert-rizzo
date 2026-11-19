"""Plan de cumplimiento del Anexo Entrega 2 (19/11): arreglos P0/P1 detectados en la auditoría."""

from datetime import datetime, timezone

import pytest
from planta_contrato import EstadoOrden, EventoSistema, NotaCierre, OrdenSistema, TipoEvento

from sistema import agente
from sistema.procesamiento.ordenes import orden_inmediata

AHORA = datetime.now(timezone.utc)


class HttpEstado:
    def __init__(self, bombas):
        self._bombas = bombas

    def estado(self):
        return {"bombas": [{"tag": t, "estado": e} for t, e in self._bombas.items()]}


def _evento(equipo="B-102", resumen="presión de descarga alta"):
    return EventoSistema(id=f"EVT-SENSOR-{equipo}-presion_descarga-x", ts=AHORA, tipo=TipoEvento.escalado,
                         equipo=equipo, origen="sensor", resumen=resumen)


# --- P0.3: reduce_inyeccion según la reserva real ------------------------------------------

def test_reduce_inyeccion_si_detiene_y_no_hay_reserva():
    orden = orden_inmediata(_evento(), HttpEstado({"B-101": "marcha", "B-102": "marcha", "B-103": "disparo"}))
    assert orden.detiene_equipo is True  # I-VAL detiene
    assert orden.reduce_inyeccion is True


def test_no_reduce_inyeccion_si_hay_reserva_parada():
    orden = orden_inmediata(_evento(), HttpEstado({"B-101": "marcha", "B-102": "marcha", "B-103": "parada"}))
    assert orden.reduce_inyeccion is False


def test_no_reduce_inyeccion_si_la_tarea_no_detiene():
    orden = orden_inmediata(_evento(resumen="vibración alta"), HttpEstado({"B-102": "marcha"}))
    assert orden.detiene_equipo is False  # I-VIB no detiene
    assert orden.reduce_inyeccion is False


# --- P0.1/P0.2: cierre ---------------------------------------------------------------------

def _orden(estado):
    return OrdenSistema(id="ORD-1", ts=AHORA, estado=estado, equipo="B-102", evento_ids=["EVT-1"],
                        tareas=["T-B-EMP"], detiene_equipo=True, reduce_inyeccion=False, ejecutor="propio",
                        repuestos=[{"codigo": "TX-EMP-KIT", "cantidad": 1}])


class _Conn:
    def close(self):
        pass


@pytest.fixture
def cierre_fake(monkeypatch):
    guardadas = []
    estado = {}
    monkeypatch.setattr(agente.persistencia_ordenes, "conectar", lambda: _Conn())
    monkeypatch.setattr(agente.persistencia_ordenes, "cargar", lambda conn, id_: estado.get("orden"))
    monkeypatch.setattr(agente.persistencia_ordenes, "guardar",
                        lambda conn, orden, pendiente_publicar=True: guardadas.append(orden))
    monkeypatch.setattr(agente, "encolar_o_analizar", lambda *a, **k: None)

    class _Extractor:
        def extraer(self, texto):
            from sistema.llm.cierre import CierreExtraido
            return CierreExtraido(tareas=["T-B-EMP"], repuestos=[], horas=4)

    monkeypatch.setattr(agente, "extractor_cierre", _Extractor(), raising=False)
    return estado, guardadas


def _nota_cierre():
    return NotaCierre(id="CIE-1", ts=AHORA, orden_id="ORD-1", tecnico="Pérez", empresa="propio", audio_url=None, fotos=[])


def test_cierre_de_orden_aprobada_pasa_a_ejecutada(cierre_fake):
    estado, guardadas = cierre_fake
    estado["orden"] = _orden(EstadoOrden.aprobada)
    agente._procesar_cierre(None, _nota_cierre())
    assert [o.estado for o in guardadas] == [EstadoOrden.ejecutada]


@pytest.mark.parametrize("previo", [EstadoOrden.pendiente, EstadoOrden.rechazada, EstadoOrden.cerrada,
                                    EstadoOrden.certificada, EstadoOrden.ejecutada])
def test_cierre_de_orden_no_aprobada_no_cambia_estado(cierre_fake, previo):
    estado, guardadas = cierre_fake
    estado["orden"] = _orden(previo)
    agente._procesar_cierre(None, _nota_cierre())
    assert guardadas == []


def test_procesar_cierre_con_corte_no_levanta_name_error(cierre_fake, monkeypatch):
    """Bug de la auditoría: el log final usaba `diferencias`, que no existía en esta función."""
    estado, guardadas = cierre_fake
    estado["orden"] = _orden(EstadoOrden.aprobada)
    agente._procesar_cierre_con_corte(None, _nota_cierre())
    agente._pool_ordenes.submit(lambda: None).result(timeout=5)  # espera a que termine el cierre
    assert [o.estado for o in guardadas] == [EstadoOrden.ejecutada]


# --- P1.4: las 5 causas repetidas del anexo -------------------------------------------------

from sistema.llm.analisis import _hint_causa_repetida  # noqa: E402


def test_hint_b103_empaquetadura_repetida_es_embolo_rayado():
    previas = [{"equipo": "B-103", "tareas": ["T-B-EMP"]}, {"equipo": "B-103", "tareas": ["T-B-EMP"]}]
    assert "émbolo" in _hint_causa_repetida("B-103", previas)


def test_hint_b102_cavitacion_repetida_por_codigo_es_filtro_primario():
    previas = [{"equipo": "B-102", "tareas": [], "codigo_falla": "F09"},
               {"equipo": "B-102", "tareas": [], "codigo_falla": "F09"}]
    assert "filtro primario" in _hint_causa_repetida("B-102", previas)


def test_hint_b102_cavitacion_repetida_por_texto_libre_de_campo():
    """El historial es texto libre: 'cavita'/'cavitacion' sin código F09 también cuenta."""
    previas = [{"equipo": "B-102", "tareas": ["I-VAL"], "codigo_falla": None, "aviso": "bba 2 cavita"},
               {"equipo": "B-102", "tareas": [], "codigo_falla": None, "trabajo_realizado": "ruido de cavitacion"}]
    assert "filtro primario" in _hint_causa_repetida("B-102", previas)


def test_hint_valvulas_en_dos_bombas_usa_el_historial_reciente_de_toda_la_planta():
    """Antes el contexto solo pedía el historial del propio equipo: el patrón cruzado no podía darse."""
    propias = [{"equipo": "B-102", "tareas": ["T-B-VAL"]}]
    recientes = [{"equipo": "B-101", "tareas": ["T-B-VAL"]}, {"equipo": "B-102", "tareas": ["T-B-VAL"]}]
    assert _hint_causa_repetida("B-102", propias) is None
    assert "F-201B" in _hint_causa_repetida("B-102", propias, recientes)


def test_patron_cruzado_cuenta_solo_bombas():
    recientes = [{"equipo": "F-201A", "tareas": ["T-B-VAL"]}, {"equipo": "B-102", "tareas": ["T-B-VAL"]}]
    assert _hint_causa_repetida("B-102", [], recientes) is None


# --- P1.5/P1.6: cierre (G) contra los casos de cierres_ejemplo.json ---------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

from planta_contrato import Repuesto  # noqa: E402

from sistema.llm.cierre import CierreExtraido, ExtractorCierre, resultado_prueba_del_texto  # noqa: E402
from sistema.procesamiento.certificacion import calcular_monto_habilita_usd  # noqa: E402
from sistema.procesamiento.cierres import comparar_cierre_con_orden, horas_incluidas  # noqa: E402

_EJEMPLOS = Path(__file__).resolve().parents[2] / "academia2026-tp/datos/documentos/contrato/cierres_ejemplo.json"


def _orden_ejemplo(caso):
    return OrdenSistema(
        id=caso["orden_id"], ts=datetime.fromisoformat(caso["fecha_inicio"]), estado=EstadoOrden.ejecutada,
        equipo=caso["equipo"], evento_ids=["EVT-X"], tareas=caso["tareas"], detiene_equipo=False,
        reduce_inyeccion=False, ejecutor="contratista",
        repuestos=[Repuesto(**r) for r in caso["repuestos_orden"]],
    )


def _cierre_ejemplo(caso):
    return CierreExtraido(
        tareas=caso["tareas"], repuestos=[Repuesto(**r) for r in caso["repuestos_declarados"]],
        horas=caso["horas_declaradas"], resultado_prueba=resultado_prueba_del_texto(caso["cierre_texto"]),
    )


def _casos():
    if not _EJEMPLOS.exists():
        pytest.skip("academia2026-tp no está al lado de este repo")
    return {c["orden_id"]: c for c in json.loads(_EJEMPLOS.read_text(encoding="utf-8"))}


def test_resultado_de_la_prueba_se_extrae_del_texto():
    assert resultado_prueba_del_texto("Fueron 2 horas. Probé a 110 kilos, sin pérdida. Listo.") == \
        "Probé a 110 kilos, sin pérdida"
    assert resultado_prueba_del_texto("Medí las tres bombas.") is None


def test_extractor_completa_la_prueba_si_el_llm_no_la_trae():
    class _Chat:
        def invoke(self, mensajes, config=None):
            return CierreExtraido(tareas=["T-B-EMP"])
    cierre = ExtractorCierre(_Chat()).extraer("Cambié la empaquetadura. Probé a ciento diez kilos, sin pérdida.")
    assert cierre.resultado_prueba == "Probé a ciento diez kilos, sin pérdida"


def test_horas_extra_ot_24_0015_se_marcan():
    caso = _casos()["OT-24-0015"]
    motivos = comparar_cierre_con_orden(_cierre_ejemplo(caso), _orden_ejemplo(caso))
    assert any("horas extra" in m and "26.5" in m for m in motivos)
    assert calcular_monto_habilita_usd(_orden_ejemplo(caso)) == caso["monto_habilita_usd"]


def test_repuesto_no_autorizado_ot_25_0004_se_marca_y_no_se_paga():
    caso = _casos()["OT-25-0004"]
    motivos = comparar_cierre_con_orden(_cierre_ejemplo(caso), _orden_ejemplo(caso))
    assert any("TX-SELLO-CAR" in m for m in motivos)
    assert not any("horas extra" in m for m in motivos)
    assert calcular_monto_habilita_usd(_orden_ejemplo(caso)) == caso["monto_habilita_usd"]


def test_lista_vieja_ot_24_0010_se_paga_con_la_lista_2022():
    caso = _casos()["OT-24-0010"]
    assert calcular_monto_habilita_usd(_orden_ejemplo(caso)) == caso["monto_habilita_usd"] == 840


def test_todos_los_ejemplos_el_habilita_sale_de_la_orden_no_de_lo_declarado():
    for caso in _casos().values():
        monto = calcular_monto_habilita_usd(_orden_ejemplo(caso))
        assert monto == caso["monto_habilita_usd"], caso["orden_id"]


def test_movilizacion_mal_cobrada_no_entra_al_habilita():
    """Los casos con movilización cobrada aparte declaran más de lo que se habilita."""
    casos = [c for c in _casos().values() if any("moviliz" in d.lower() for d in c["diferencias"])]
    assert casos, "cierres_ejemplo.json debería traer al menos un caso de movilización"
    for caso in casos:
        assert calcular_monto_habilita_usd(_orden_ejemplo(caso)) == caso["monto_habilita_usd"] < caso["monto_declarado_usd"]


def test_tarea_pedida_no_hecha_y_repuesto_reservado_no_usado():
    orden = _orden(EstadoOrden.aprobada).model_copy(update={"tareas": ["T-B-EMP", "T-B-ACE"]})
    cierre = CierreExtraido(tareas=["T-B-EMP"], repuestos=[], resultado_prueba="probé a 110, sin pérdida")
    motivos = comparar_cierre_con_orden(cierre, orden)
    assert any("T-B-ACE" in m and "no la declara" in m for m in motivos)
    assert any("TX-EMP-KIT" in m and "0 usados" in m for m in motivos)


def test_prueba_faltante_o_con_perdida():
    orden = _orden(EstadoOrden.aprobada)  # T-B-EMP detiene el equipo
    usado = [Repuesto(codigo="TX-EMP-KIT", cantidad=1)]
    sin_prueba = comparar_cierre_con_orden(CierreExtraido(tareas=["T-B-EMP"], repuestos=usado), orden)
    assert sin_prueba == ["no informa el resultado de la prueba final"]
    con_perdida = comparar_cierre_con_orden(
        CierreExtraido(tareas=["T-B-EMP"], repuestos=usado, resultado_prueba="probé a 110, todavía pierde"), orden)
    assert any("no salió bien" in m for m in con_perdida)


def test_horas_incluidas_del_catalogo():
    assert horas_incluidas(["T-B-ROD"]) == 16
    assert horas_incluidas(["I-VIB"]) == 3
    assert horas_incluidas(["NO-ESTANDAR"]) is None


# --- P1.7: correlation ID y repuestos con disponibilidad/costo ---------------------------------

from sistema.persistencia.ordenes import correlacion_id  # noqa: E402
from sistema.procesamiento.ordenes import detalle_repuestos, sumar_evento  # noqa: E402


def test_correlacion_id_es_el_evento_raiz_y_no_cambia_al_sumar_eventos():
    orden = _orden(EstadoOrden.pendiente).model_copy(update={"evento_ids": ["EVT-RAIZ"]})
    assert correlacion_id(orden) == "EVT-RAIZ"
    assert correlacion_id(sumar_evento(orden, "EVT-OTRO")) == "EVT-RAIZ"


def test_detalle_repuestos_disponibilidad_y_costo():
    orden = _orden(EstadoOrden.pendiente).model_copy(update={"repuestos": [
        Repuesto(codigo="TX-VAL-KIT", cantidad=1),   # yacimiento 1
        Repuesto(codigo="TX-ROD-CIG", cantidad=1),   # yacimiento 0, central 2
        Repuesto(codigo="TX-EMP-KIT", cantidad=50),  # no alcanza en ningún lado
    ]})
    d = {r["codigo"]: r for r in detalle_repuestos(orden)}
    assert d["TX-VAL-KIT"]["disponibilidad"] == "yacimiento" and d["TX-VAL-KIT"]["costo_unitario_usd"] == 1900
    assert d["TX-ROD-CIG"]["disponibilidad"] == "central" and d["TX-ROD-CIG"]["costo_unitario_usd"] == 2650
    assert d["TX-EMP-KIT"]["disponibilidad"] == "sin stock" and d["TX-EMP-KIT"]["costo_total_usd"] == 420 * 50


# --- P1.8: detección de loops en el diálogo ------------------------------------------------

from sistema.dominio.parte import EstadoParte, FilaParteInterna, Parte  # noqa: E402
from sistema.procesamiento.dialogo import (  # noqa: E402
    MAX_REPETICIONES_PREGUNTA,
    MAX_TURNOS_DIALOGO,
    NOTA_LOOP,
    EstadoDialogo,
    construir_mensaje,
    detectar_loop,
)
from sistema.persistencia.memoria import _estado_a_json, _json_a_estado  # noqa: E402


def _estado_dialogo():
    fila = FilaParteInterna(hora=AHORA, tag=None, tag_dicho="la bomba", variable="presión", valor=110,
                            unidad="kg/cm2", ambiguo=True, candidatos=["B-101", "B-102"])
    parte = Parte(id="PARTE-N1", nota_ids=["N1"], estado=EstadoParte.borrador, filas=[fila])
    return EstadoDialogo(parte=parte)


def test_misma_pregunta_repetida_corta_el_dialogo():
    estado = _estado_dialogo()
    for _ in range(MAX_REPETICIONES_PREGUNTA):
        assert detectar_loop(estado, construir_mensaje(estado)) is False
    assert detectar_loop(estado, construir_mensaje(estado)) is True
    assert estado.parte.estado == EstadoParte.confirmado
    fila = estado.parte.filas[0]
    assert fila.tag == "B-101" and NOTA_LOOP in fila.observacion


def test_no_corta_antes_del_tope_de_desambiguacion_existente():
    from sistema.procesamiento.dialogo import MAX_INTENTOS_DESAMBIGUACION
    assert MAX_REPETICIONES_PREGUNTA >= MAX_INTENTOS_DESAMBIGUACION


def test_demasiados_turnos_corta_el_dialogo():
    estado = _estado_dialogo()
    estado.firmas_preguntas = [f"otra-{i}" for i in range(MAX_TURNOS_DIALOGO)]
    assert detectar_loop(estado, construir_mensaje(estado)) is True


def test_firmas_sobreviven_a_un_reinicio():
    estado = _estado_dialogo()
    detectar_loop(estado, construir_mensaje(estado))
    recuperado = _json_a_estado(_estado_a_json(estado))
    assert recuperado.firmas_preguntas == estado.firmas_preguntas
    datos_viejos = _estado_a_json(estado)
    del datos_viejos["firmas_preguntas"]  # diálogo guardado antes de este cambio
    assert _json_a_estado(datos_viejos).firmas_preguntas == []


# --- P1.9: visión por componente y foto embebida -------------------------------------------

from sistema.llm.vision import AnalisisFoto, ClienteNvidiaNIM, _extraer_json, imagen_como_data_uri  # noqa: E402


class _Resp:
    def __init__(self, content=b"", headers=None, payload=None):
        self.content, self.headers, self._payload = content, headers or {}, payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_foto_de_la_planta_se_manda_embebida_no_por_url_interna():
    pedidos = []

    def get(url, timeout):
        pedidos.append(url)
        return _Resp(b"\xff\xd8jpeg", {"content-type": "image/jpeg"})

    uri = imagen_como_data_uri("/data/notas/N1/foto-0.jpg", "http://sim:8100", get=get)
    assert pedidos == ["http://sim:8100/data/notas/N1/foto-0.jpg"]
    assert uri.startswith("data:image/jpeg;base64,")


def test_respuesta_de_vision_con_texto_alrededor_y_componente_normalizado():
    datos = _extraer_json('Acá va:\n```json\n{"componente": "Manómetro", "estado": "normal", "hallazgo": "aguja en 110",'
                          ' "lectura_manometro": 110, "danio_estructural": false, "descripcion": "manómetro"}\n```')
    analisis = AnalisisFoto.model_validate(datos)
    assert analisis.componente == "manometro" and analisis.lectura_manometro == 110


def test_componente_desconocido_queda_como_otro_y_formato_viejo_sigue_valiendo():
    assert AnalisisFoto(danio_estructural=False, descripcion="x", componente="válvula").componente == "otro"
    assert AnalisisFoto(danio_estructural=True, descripcion="grieta").componente == "otro"


def test_cliente_nim_completo_con_http_falso(monkeypatch):
    import sistema.llm.vision as vision
    enviado = {}

    def post(url, headers, json, timeout):
        enviado.update(json)
        contenido = ('{"componente": "acople", "estado": "falla", "hallazgo": "elastómero roto",'
                     ' "lectura_manometro": null, "danio_estructural": false, "descripcion": "acople"}')
        return _Resp(payload={"choices": [{"message": {"content": contenido}}]})

    monkeypatch.setattr(vision.httpx, "post", post)
    monkeypatch.setattr(vision, "imagen_como_data_uri", lambda url: "data:image/jpeg;base64,AAA")
    analisis = ClienteNvidiaNIM(api_key="k", modelo="m").analizar("/data/x.jpg")
    assert analisis.componente == "acople" and analisis.estado == "falla"
    assert enviado["messages"][0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,AAA"


# --- G1: tarea estándar cuando la evidencia es clara ------------------------------------------

from sistema.procesamiento.ordenes import tarea_estandar_clara  # noqa: E402
from sistema.procesamiento.reglas_aprobacion import hay_reserva_disponible  # noqa: E402


def _ev(resumen, equipo="B-102", origen="parte", id_=None):
    return EventoSistema(id=id_ or f"EVT-PARTE-PARTE-N1-{equipo}", ts=AHORA, tipo=TipoEvento.escalado,
                         equipo=equipo, origen=origen, resumen=resumen)


@pytest.mark.parametrize("resumen,equipo,esperado", [
    ("El operador informa en B-102: pérdida por empaquetadura, pérdida", "B-102", "T-B-EMP"),
    ("El operador informa en B-101: nivel de aceite bajo", "B-101", "T-B-ACE"),
    ("El operador informa en B-103: correas en mal estado", "B-103", "T-B-COR"),
    ("El operador informa en F-201A: diferencial alto", "F-201A", "T-F-CAR"),
    # evidencia mezclada o no concluyente → inspección
    ("El operador informa en B-102: pérdida", "B-102", None),
    ("El operador informa en B-102: pérdida por empaquetadura, golpeteo metálico", "B-102", None),
    ("El operador informa en B-101: nivel de aceite bajo, pérdida", "B-101", None),
    ("Valores fuera de límite en la recorrida: x. El operador informa en B-102: pérdida por empaquetadura",
     "B-102", None),
    ("El operador informa en F-201A: pérdida por empaquetadura", "F-201A", None),  # no es tarea de un filtro
])
def test_tarea_estandar_clara_por_lo_dicho(resumen, equipo, esperado):
    assert tarea_estandar_clara(_ev(resumen, equipo)) == esperado


def test_tarea_estandar_clara_por_sensor_de_diferencial():
    ev = _ev("F-201A/presion_diferencial sostenido en nivel alarma", "F-201A", "sensor",
             "EVT-SENSOR-F-201A-presion_diferencial-x")
    assert tarea_estandar_clara(ev) == "T-F-CAR"
    vib = _ev("B-101/vibracion sostenida", "B-101", "sensor", "EVT-SENSOR-B-101-vibracion-x")
    assert tarea_estandar_clara(vib) is None


def test_con_tarea_estandar_y_reglas_ok_se_aprueba_sola():
    ev = _ev("F-201A/presion_diferencial sostenido", "F-201A", "sensor", "EVT-SENSOR-F-201A-presion_diferencial-x")
    orden = orden_inmediata(ev, HttpEstado({"B-101": "marcha", "B-102": "marcha", "B-103": "mantenimiento"}),
                            tarea_estandar=True)
    assert orden.estado == EstadoOrden.aprobada
    assert orden.tareas == ["T-F-CAR"]
    assert [(r.codigo, r.cantidad) for r in orden.repuestos] == [("FC-CART-25", 48)]


def test_f201b_no_se_aprueba_por_stock_de_cartuchos():
    ev = _ev("El operador informa en F-201B: diferencial alto", "F-201B")
    orden = orden_inmediata(ev, HttpEstado({"B-101": "marcha"}), tarea_estandar=True)
    assert orden.estado == EstadoOrden.pendiente and orden.tareas == ["T-F-CAR"]
    assert orden.repuestos[0].codigo == "FC-CART-10"
    assert "T-F-CAR propuesta por evidencia clara" in orden.motivo


def test_empaquetadura_sin_reserva_queda_pendiente_y_con_reserva_se_aprueba():
    ev = _ev("El operador informa en B-102: pérdida por empaquetadura, pérdida")
    sin = orden_inmediata(ev, HttpEstado({"B-101": "marcha", "B-102": "marcha", "B-103": "mantenimiento"}),
                          tarea_estandar=True)
    assert sin.estado == EstadoOrden.pendiente and sin.reduce_inyeccion is True
    con = orden_inmediata(ev, HttpEstado({"B-101": "marcha", "B-102": "marcha", "B-103": "parada"}),
                          tarea_estandar=True)
    assert con.estado == EstadoOrden.aprobada and con.reduce_inyeccion is False


def test_alerta_de_seguridad_fuerza_revision_humana():
    ev = _ev("El operador informa en B-101: pérdida por empaquetadura", "B-101")
    orden = orden_inmediata(ev, HttpEstado({"B-101": "marcha", "B-103": "parada"}), tarea_estandar=True,
                            siempre_pendiente=frozenset({"B-101"}))
    assert orden.estado == EstadoOrden.pendiente


def test_sin_el_interruptor_sigue_siendo_inspeccion():
    ev = _ev("El operador informa en B-102: pérdida por empaquetadura")
    assert orden_inmediata(ev, HttpEstado({"B-103": "parada"})).tareas == ["I-VAL"]


@pytest.mark.parametrize("propio,esperado", [("marcha", False), ("parada", True), ("disparo", True),
                                             ("mantenimiento", True)])
def test_reserva_solo_hace_falta_si_el_equipo_esta_en_marcha(propio, esperado):
    """Mismo criterio que el corrector: detener un equipo que ya no inyecta no reduce la inyección."""
    http = HttpEstado({"B-101": "marcha", "B-102": propio, "B-103": "mantenimiento"})
    assert hay_reserva_disponible("B-102", http) is esperado
