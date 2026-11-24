"""Lote 6: velocidad y fiabilidad. Bloque C con transcripciones reales de las corridas
`vision-nemotron` y `cpu4`; los bloques A/B/D agregan sus tests abajo."""

from datetime import datetime, timezone

import pytest

from sistema.procesamiento.novedades import novedades_en
from sistema.procesamiento.parser_notas import ParserPartes, catalogo_desde_planta, normalizar_whisper

from tests.test_lote5 import PLANTA

AHORA = datetime.now(timezone.utc)
PARSER = ParserPartes(catalogo_desde_planta(PLANTA))


# --- C1: golpeteo deformado por Whisper ------------------------------------------------------

@pytest.mark.parametrize("texto", [
    "La pistola hace un colqueteo metálico.",  # tormenta-perfecta NOTA-00005 (vision-nemotron)
    "hace un golpecheo metálico",  # tormenta-perfecta NOTA-00008
    "Hacia un golpe teo metálico.",
    "Hace un golpeteo metálico.",
    "hace un golpe de metálico",  # benchmark de Whisper (Lote 6, A1)
    "la dosa hace un volteo metálico",
    "con la pistola hace un golpe telemetálico.",
])
def test_golpeteo_deformado_es_novedad(texto):
    assert "golpeteo metálico" in novedades_en(texto)


def test_otras_palabras_en_teo_no_son_golpeteo():
    assert "golpeteo metálico" not in novedades_en("Hice el chequeo de la bomba, todo normal.")
    assert normalizar_whisper("el chequeo general") == "el chequeo general"


# --- C4: normalizaciones nuevas ------------------------------------------------------------

@pytest.mark.parametrize("texto", [
    "El filtro B-Mario 10 de diferencial",  # ataque NOTA-00005 (vision-nemotron)
    "El filtro Bmedio 10 de diferencial.",
    "El filtro BME10 de diferencial",
])
def test_filtro_b_me_dio_deformado_pregunta_el_valor_dudoso(texto):
    filas = PARSER.parsear(texto, AHORA, [])
    assert filas is not None, "no debe caer al LLM"
    assert len(filas) == 1
    fila = filas[0]
    assert fila.tag == "F-201B"
    assert fila.valor == 1.0 and fila.ambiguedad_valor  # "diez" de diferencial → 1.0 a confirmar


def test_filtro_b_me_dio_con_decimal_no_pregunta():
    filas = PARSER.parsear("El filtro Bmedio 0.9 de diferencial", AHORA, [])
    assert [(f.tag, f.valor, f.ambiguedad_valor) for f in filas] == [("F-201B", 0.9, False)]


def test_filtro_b_sin_numero_no_se_toca():
    assert normalizar_whisper("el filtro B está limpio") == "el filtro B está limpio"


def test_esta_del_fondo_es_la_del_fondo():
    filas = PARSER.parsear("Está del fondo está parada, en reserva.", AHORA, [])
    assert filas is not None
    assert [f.tag for f in filas] == ["B-103"]


def test_la_del_medio_seguida_de_numero_no_pierde_el_equipo():
    # Bug latente: la regla de "medió 62" convertía "la del medio 115" en "la del me dio 115".
    assert normalizar_whisper("la del medio 115 kilos") == "la del medio 115 kilos"
    assert normalizar_whisper("La del medio medió 115") == "La del medio me dio 115"


# --- D2: orquestación del agente (bloques A y B) --------------------------------------------

import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

import paho.mqtt.client as mqtt  # noqa: E402
from planta_contrato import EstadoOrden, NotaRecorrida  # noqa: E402

from sistema import agente  # noqa: E402
from sistema.config import config  # noqa: E402
from sistema.llm import ollama as ollama_mod  # noqa: E402
from sistema.persistencia import conexion  # noqa: E402
from sistema.procesamiento.recorridas import descargar_y_transcribir  # noqa: E402

from tests.test_recorridas import HttpFalso, TranscriptorFalso  # noqa: E402


class _Info:
    def __init__(self, publicado: bool, rc=mqtt.MQTT_ERR_SUCCESS):
        self.rc = rc
        self._publicado = publicado

    def wait_for_publish(self, timeout=None):
        pass

    def is_published(self):
        return self._publicado


class _ClienteFalso:
    def __init__(self, publicado: bool = True):
        self.publicados: list[tuple[str, str, int]] = []
        self._publicado = publicado

    def publish(self, topico, payload, qos=0):
        self.publicados.append((topico, payload, qos))
        return _Info(self._publicado)


class _Conn:
    def close(self):
        pass


def _orden_pendiente():
    from tests.test_cumplimiento_e2 import _orden

    return _orden(EstadoOrden.pendiente)


def _una_vuelta_de_reconciliacion(monkeypatch, cliente):
    parar = threading.Event()
    marcadas = []

    def pendientes(conn):
        parar.set()  # una sola vuelta
        return [(_orden_pendiente(), "v1")]

    monkeypatch.setattr(agente.persistencia_ordenes, "conectar", lambda: _Conn())
    monkeypatch.setattr(agente.persistencia_ordenes, "pendientes_de_publicar_con_version", pendientes)
    monkeypatch.setattr(agente.persistencia_ordenes, "marcar_publicada",
                        lambda conn, id_, version=None: marcadas.append((id_, version)) or True)
    agente.reconciliar_ordenes(cliente, parar)
    return marcadas


def test_orden_no_confirmada_por_el_broker_no_se_marca_publicada(monkeypatch):
    # B2: antes se marcaba siempre, y con el broker caído la orden no salía nunca.
    assert _una_vuelta_de_reconciliacion(monkeypatch, _ClienteFalso(publicado=False)) == []


def test_orden_confirmada_se_marca_con_su_version(monkeypatch):
    cliente = _ClienteFalso(publicado=True)
    assert _una_vuelta_de_reconciliacion(monkeypatch, cliente) == [("ORD-1", "v1")]
    assert cliente.publicados[0][2] == 1  # QoS 1


def test_on_message_nunca_propaga_una_excepcion(monkeypatch):
    # B1: paho relanza las excepciones de los callbacks y `loop_forever` terminaba.
    def explota(client, msg):
        raise RuntimeError("simulador caído")

    monkeypatch.setattr(agente, "_on_message", explota)
    msg = mqtt.MQTTMessage(topic=b"planta/recorridas")
    agente.on_message(None, None, msg)  # no levanta


class _Lectura:
    tag = "B-101"
    variable = "vibracion"


def test_lectura_de_sensor_con_error_no_corta_nada(monkeypatch):
    class _Detector:
        def procesar(self, lectura):
            raise RuntimeError("historiador caído")

    monkeypatch.setattr(agente, "detector_sensores", _Detector())
    monkeypatch.setattr(agente, "_lecturas_en_cola", 1)
    agente._procesar_lectura(_ClienteFalso(), _Lectura())
    assert agente._lecturas_en_cola == 0


def test_latido_sobrevive_a_un_error_y_no_informa_cero_si_postgres_falla(monkeypatch):
    # B4: sin try/except, un error mataba el hilo del latido para siempre.
    parar = threading.Event()
    llamadas = {"n": 0}
    pendientes = iter([3, None])  # segunda vuelta: Postgres no responde

    def enlace(timeout=2.0):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise RuntimeError("falla rara")
        if llamadas["n"] >= 3:
            parar.set()
        return False

    monkeypatch.setattr(agente, "enlace_arriba", enlace)
    monkeypatch.setattr(agente, "_contar_pendientes", lambda: next(pendientes))
    monkeypatch.setattr(config, "intervalo_sondeo_estado_s", 0.01)
    monkeypatch.setattr(config, "intervalo_latido_s", 0.0)
    monkeypatch.setattr(config, "espera_primer_latido_s", 0.0)
    cliente = _ClienteFalso()
    agente.publicar_estado(cliente, parar)
    latidos = [json.loads(p) for t, p, q in cliente.publicados]
    assert [lat["pendientes_sincronizar"] for lat in latidos] == [3, 3]


def test_el_corte_se_cuenta_desde_que_la_nota_arranca():
    # A6: una nota que solo esperó su turno en la cola no es un "TIMEOUT".
    pool = ThreadPoolExecutor(max_workers=1)
    pool.submit(time.sleep, 0.4)
    futuro, arranque = agente._enviar_con_arranque(pool, time.sleep, 0.05)
    assert agente._vigilar("nota X", futuro, arranque, limite_s=0.3) is True
    pool.shutdown()


def test_corte_real_cuando_la_nota_misma_tarda():
    pool = ThreadPoolExecutor(max_workers=1)
    futuro, arranque = agente._enviar_con_arranque(pool, time.sleep, 0.5)
    assert agente._vigilar("nota X", futuro, arranque, limite_s=0.1) is False
    pool.shutdown()


def _nota(id_="NOTA-77"):
    return NotaRecorrida(id=id_, ts=AHORA, operador="Juan", audio_url="/a.wav", fotos=[], recorrida_id="R-1")


def test_acuse_si_la_nota_no_tuvo_respuesta_a_tiempo(monkeypatch):
    monkeypatch.setattr(config, "acuse_nota_s", 0.05)
    pool = ThreadPoolExecutor(max_workers=1)
    futuro, arranque = agente._enviar_con_arranque(pool, time.sleep, 0.2)
    cliente = _ClienteFalso()
    agente._vigilar_nota(cliente, _nota("NOTA-ACUSE"), futuro, arranque)
    tablet = [json.loads(p) for t, p, q in cliente.publicados if t == "sistema/tablet"]
    assert [m["id"] for m in tablet] == ["ACUSE-NOTA-ACUSE"]
    assert tablet[0]["espera_respuesta"] is False  # no cuenta como pregunta (A4b)
    pool.shutdown()


def test_sin_acuse_si_la_nota_ya_contesto(monkeypatch):
    monkeypatch.setattr(config, "acuse_nota_s", 0.3)
    pool = ThreadPoolExecutor(max_workers=1)
    futuro, arranque = agente._enviar_con_arranque(pool, lambda: agente._evento_mensaje("NOTA-RAPIDA").set())
    cliente = _ClienteFalso()
    agente._vigilar_nota(cliente, _nota("NOTA-RAPIDA"), futuro, arranque)
    assert [t for t, p, q in cliente.publicados] == []
    pool.shutdown()


def test_nota_repetida_por_el_broker_se_ignora():
    assert agente._nota_nueva("NOTA-DUP") is True
    assert agente._nota_nueva("NOTA-DUP") is False


def test_las_fotos_no_se_bajan_antes_de_transcribir(tmp_path, monkeypatch):
    # A5: una foto lenta no puede demorar la alerta de seguridad.
    monkeypatch.setattr(config, "media_dir", tmp_path)
    http = HttpFalso()
    nota = NotaRecorrida(id="N-1", ts=AHORA, operador="J", audio_url="/a.wav", fotos=["/f1.jpg"], recorrida_id="R")
    tiempos = {}
    descargar_y_transcribir(nota, http, TranscriptorFalso(), tiempos)
    assert http.pedidos == ["/a.wav"]
    assert {"descarga", "whisper", "audio"} <= set(tiempos)


def test_cancelar_baja_prioridad_corta_y_cuenta():
    ollama_mod.soltar_baja_prioridad()
    antes = ollama_mod.generacion_cancelacion()
    ollama_mod.chat_ollama(timeout_s=1.0, baja_prioridad=True)
    assert ollama_mod.cancelar_baja_prioridad() == 1
    assert ollama_mod.generacion_cancelacion() == antes + 1
    # Sin nada en curso, cancelar no cuenta como corte (una sugerencia no se reintenta de más).
    assert ollama_mod.cancelar_baja_prioridad() == 0
    assert ollama_mod.generacion_cancelacion() == antes + 1


def test_soltar_no_cuenta_como_cancelacion():
    antes = ollama_mod.generacion_cancelacion()
    ollama_mod.chat_ollama(timeout_s=1.0, baja_prioridad=True)
    ollama_mod.soltar_baja_prioridad()
    assert ollama_mod.generacion_cancelacion() == antes
    assert ollama_mod.cancelar_baja_prioridad() == 0


def test_ddl_una_sola_vez_por_proceso(monkeypatch):
    # B6: antes cada conexión (el latido abre dos cada 2 s) corría CREATE/ALTER TABLE.
    ejecutados = []

    class _ConnFalsa:
        def execute(self, sql, *a):
            ejecutados.append(sql)

        def close(self):
            pass

    conexion.olvidar_ddl()
    monkeypatch.setattr(conexion.psycopg, "connect", lambda *a, **k: _ConnFalsa())
    conexion.conectar("prueba", "CREATE TABLE x ()")
    conexion.conectar("prueba", "CREATE TABLE x ()")
    assert ejecutados == ["CREATE TABLE x ()"]
    conexion.olvidar_ddl()


def test_conexion_con_timeouts(monkeypatch):
    recibidos = {}
    monkeypatch.setattr(conexion.psycopg, "connect", lambda dsn, **k: recibidos.update(k) or _Conn())
    conexion.abrir()
    assert recibidos["connect_timeout"] == config.postgres_connect_timeout_s
    assert "statement_timeout" in recibidos["options"]


def test_reintentos_terminan_bien_si_postgres_vuelve(monkeypatch):
    monkeypatch.setattr(agente.time, "sleep", lambda s: None)
    intentos = {"n": 0}

    def guardar():
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise OSError("postgres caído")
        return "ok"

    assert agente._con_reintentos("orden X", guardar) == "ok"
    assert intentos["n"] == 3


# --- A2: embeddings precalculados ------------------------------------------------------------

from sistema.rag import indexar as indexar_mod  # noqa: E402


class _EmbeddingsFalsos:
    def __init__(self):
        self.textos = []

    def embed_documentos(self, textos):
        self.textos.extend(textos)
        return [[float(len(t)), 1.0] for t in textos]


class _HttpCorpus:
    def fragmentos(self):
        return [
            {"id": "a", "doc": "D", "titulo": "T", "texto": "uno"},
            {"id": "b", "doc": "D", "titulo": "T2", "texto": "dos dos"},
            {"id": "c", "doc": "D", "titulo": "T", "texto": "uno"},
        ]


def test_cache_de_embeddings_ida_y_vuelta(tmp_path):
    ruta = tmp_path / "cache.npz"
    emb = _EmbeddingsFalsos()
    assert indexar_mod.exportar_cache(_HttpCorpus(), emb, ruta) == 2  # textos repetidos una vez
    cache = indexar_mod.cargar_cache(ruta)
    texto = indexar_mod.texto_para_embeber("D", "T2", "dos dos")
    assert list(cache[indexar_mod.clave_cache(texto)]) == [float(len(texto)), 1.0]


def test_contextual_chunking_antepone_documento_y_seccion():
    # Lote 7 (módulo 2.2): el vector lleva "Documento › Sección" adelante.
    assert indexar_mod.texto_para_embeber("historial", "OT-25-0024 B-101 I-VIB", "vibra") == (
        "historial › OT-25-0024 B-101 I-VIB\nvibra"
    )
    assert indexar_mod.texto_para_embeber("MBP-4", None, "x") == "MBP-4\nx"


def test_clave_del_cache_cambia_con_el_modelo():
    assert indexar_mod.clave_cache("x", "nomic-embed-text") != indexar_mod.clave_cache("x", "bge-m3")


def test_sin_archivo_de_cache_no_hay_cache(tmp_path):
    assert indexar_mod.cargar_cache(tmp_path / "no-existe.npz") == {}


# --- BUGS #63: el mismo evento escala dos veces -------------------------------------------------

def test_evento_repetido_no_pisa_la_orden_ya_decidida(monkeypatch):
    from tests.test_cumplimiento_e2 import _evento, _orden

    aprobada = _orden(EstadoOrden.aprobada)
    guardadas = []
    monkeypatch.setattr(agente, "orden_inmediata", lambda *a, **k: _orden(EstadoOrden.pendiente))
    monkeypatch.setattr(agente.persistencia_ordenes, "conectar", lambda: _Conn())
    monkeypatch.setattr(agente.persistencia_ordenes, "cargar", lambda conn, id_: aprobada)
    monkeypatch.setattr(agente.persistencia_ordenes, "cargar_todas", lambda conn: [aprobada])
    monkeypatch.setattr(agente.persistencia_ordenes, "guardar", lambda conn, o, **k: guardadas.append(o))
    agente._orden_inmediata_best_effort(_evento())
    assert guardadas == []


def test_evento_nuevo_si_guarda_su_orden(monkeypatch):
    from tests.test_cumplimiento_e2 import _evento, _orden

    guardadas = []
    monkeypatch.setattr(agente, "orden_inmediata", lambda *a, **k: _orden(EstadoOrden.pendiente))
    monkeypatch.setattr(agente.persistencia_ordenes, "conectar", lambda: _Conn())
    monkeypatch.setattr(agente.persistencia_ordenes, "cargar", lambda conn, id_: None)
    monkeypatch.setattr(agente.persistencia_ordenes, "cargar_todas", lambda conn: [])
    monkeypatch.setattr(agente.persistencia_ordenes, "guardar", lambda conn, o, **k: guardadas.append(o))
    agente._orden_inmediata_best_effort(_evento())
    assert [o.estado for o in guardadas] == [EstadoOrden.pendiente]


@pytest.mark.parametrize("texto, tag", [
    ("El A1 está disparada, no arranca.", "B-101"),  # 15 veces en las corridas guardadas
    ("El A1, el rodamiento me dio 54 grados con una pistola.", "B-101"),  # lote6-velocidad NOTA-00018
])
def test_el_a1_es_la_uno(texto, tag):
    filas = PARSER.parsear(texto, AHORA, [])
    assert filas is not None, "no debe caer al LLM"
    assert {f.tag for f in filas} == {tag}


def test_el_a_con_decimal_no_se_toca():
    assert normalizar_whisper("El A 1,3 de diferencial") == "El A 1,3 de diferencial"


def test_todas_las_llamadas_a_qwen_usan_el_mismo_contexto():
    # Lote 8: Ollama recarga el modelo cada vez que cambia `num_ctx`. El precalentado, la
    # extracción y el cierre usaban el default (2048) y el análisis 4096: qwen se recargaba
    # varias veces por escenario, encima de las notas.
    from sistema.llm.analisis import CONTEXTO_ANALISIS_TOKENS

    por_defecto = ollama_mod.chat_ollama(timeout_s=1.0)
    assert por_defecto.num_ctx == CONTEXTO_ANALISIS_TOKENS
    ollama_mod.soltar_baja_prioridad()


def test_quieto_hace_cuenta_desde_el_ultimo_trabajo_urgente():
    # Lote 8: la sugerencia espera un rato de quietud, no solo que no haya una nota en curso.
    from sistema.llm.control import TrabajoUrgente

    t = TrabajoUrgente()
    with t:
        assert t.quieto_hace() == 0.0
        with t:
            pass
        assert t.quieto_hace() == 0.0  # sigue habiendo uno en curso
    time.sleep(0.05)
    assert t.quieto_hace() >= 0.05


def test_sugerencia_espera_la_quietud(monkeypatch):
    llamadas = []
    monkeypatch.setattr(config, "quietud_sugerencia_s", 3600.0)
    monkeypatch.setattr(config, "espera_maxima_sugerencia_s", 0.0)
    monkeypatch.setattr(agente, "procesar_evento_escalado", lambda *a, **k: llamadas.append(a))
    agente._sugerencia_llm(None, type("E", (), {"id": "EVT-X"})())
    assert llamadas == []  # el sistema no estuvo quieto el tiempo pedido: no usa Ollama


def test_sin_sugerencia_llm_no_encola(monkeypatch):
    encolados = []
    monkeypatch.setattr(config, "sugerencia_llm", False)
    monkeypatch.setattr(agente, "_orden_inmediata_best_effort", lambda evento: None)
    monkeypatch.setattr(agente, "_enviar_con_arranque", lambda *a, **k: encolados.append(a))
    agente._procesar_evento_escalado_con_corte(None, type("E", (), {"id": "EVT-X"})())
    assert encolados == []


# --- Lote 8: preguntas de más por manías de Whisper (lote6b) ---------------------------------

def test_cero_nueve_con_cero_adelante_no_pregunta():
    # martes NOTA-00010: "El filtro B medio 09 de diferencial" preguntaba "¿es 0.9?".
    filas = PARSER.parsear("El filtro B medio 09 de diferencial. El pulmón está en 5 metros.", AHORA, [])
    assert [(f.tag, f.valor, f.ambiguedad_valor) for f in filas] == [("F-201B", 0.9, False), ("TK-301", 5.0, False)]


def test_filtro_ve_es_el_filtro_b():
    # ataque NOTA-00008: "el filtro ve medio 0,9" preguntaba "¿a cuál filtro?" y el borrador ponía
    # el diferencial en B-101.
    texto = ("Este, la de la punta está en 111 kilos de descarga el rodamiento medio 61 grados con una "
             "pistola, además el filtro ve medio 0,9 de diferencial, el pulmón está en 5 metros.")
    filas = PARSER.parsear(texto, AHORA, [])
    assert filas is not None
    assert ("F-201B", "presion_diferencial", 0.9) in [(f.tag, f.variable, f.valor) for f in filas]
    assert not any(f.ambiguo for f in filas)


def test_horas_y_decimales_no_se_tocan():
    assert normalizar_whisper("a las 09:30 me dio 0,9") == "a las 09:30 me dio 0,9"
    assert normalizar_whisper("el filtro vez pasada") == "el filtro vez pasada"


def _latir_una_vez(monkeypatch, espera_s: float) -> tuple[list, float]:
    parar = threading.Event()

    def enlace(timeout=2.0):
        parar.set()
        return True

    monkeypatch.setattr(agente, "enlace_arriba", enlace)
    monkeypatch.setattr(agente, "_contar_pendientes", lambda: 0)
    monkeypatch.setattr(config, "espera_primer_latido_s", espera_s)
    cliente = _ClienteFalso()
    inicio = time.monotonic()
    agente.publicar_estado(cliente, parar)
    return cliente.publicados, time.monotonic() - inicio


def test_primer_latido_espera_el_arranque_completo(monkeypatch):
    # Lote 8: el corrector arranca el reloj con el primer latido; si sale antes de precalentar
    # Ollama, las primeras notas compiten con la carga de los modelos.
    agente._arranque_listo.clear()
    threading.Timer(0.3, agente._arranque_listo.set).start()
    publicados, tardo = _latir_una_vez(monkeypatch, espera_s=30.0)
    assert len(publicados) == 1
    assert 0.25 <= tardo < 5.0


def test_primer_latido_sale_al_tope_aunque_no_termine_el_arranque(monkeypatch):
    # Bug #25: no latir por minutos dejaba al corrector sin primer latido.
    agente._arranque_listo.clear()
    publicados, tardo = _latir_una_vez(monkeypatch, espera_s=0.2)
    assert len(publicados) == 1
    assert tardo < 3.0
    agente._arranque_listo.set()
