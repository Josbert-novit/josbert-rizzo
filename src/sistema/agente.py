import functools
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturoTimeoutError

import paho.mqtt.client as mqtt
from planta_contrato import (
    EstadoOrden,
    EstadoSistema,
    EventoSistema,
    LecturaSensor,
    MensajeTablet,
    NotaCierre,
    NotaRecorrida,
    RespuestaOperador,
    TipoEvento,
    Topicos,
)

from sistema.config import config
from sistema.integraciones.enlace import enlace_arriba
from sistema.integraciones.planta_http import PlantaHTTP
from sistema.llm.cierre import ExtractorCierre
from sistema.llm.control import ConsumoTokens, TrabajoUrgente, trabajo_urgente
from sistema.llm.extraccion import Extractor
from sistema.llm.ollama import cancelar_baja_prioridad, chat_ollama, generacion_cancelacion, soltar_baja_prioridad
from sistema.llm.transcripcion import Transcriptor
from sistema.persistencia import fotos as persistencia_fotos
from sistema.persistencia import memoria
from sistema.persistencia import ordenes as persistencia_ordenes
from sistema.procesamiento.cierres import comparar_cierre_con_orden
from sistema.procesamiento.parser_cierre import parsear_cierre
from sistema.procesamiento.dialogo import EstadoDialogo, construir_mensaje, detectar_loop, procesar_respuesta
from sistema.procesamiento.eventos import DetectorSensores, eventos_del_parte
from sistema.procesamiento.interpretacion import configurar_parser
from sistema.procesamiento.parser_notas import ParserPartes, catalogo_desde_planta
from sistema.procesamiento.novedades import equipos_sin_novedad_seguros, novedades_del_parte, novedades_del_texto
from sistema.procesamiento.fotos import encolar_o_analizar, reconciliar_fotos_pendientes
from sistema.procesamiento.limites import CatalogoLimites
from sistema.procesamiento.ordenes import (
    orden_abierta_equivalente,
    orden_inmediata,
    procesar_evento_escalado,
    sumar_evento,
)
from sistema.procesamiento.rango_historico import VerificadorRango
from sistema.procesamiento.recorridas import armar_parte, descargar_fotos, descargar_y_transcribir, guardar_parte
from sistema.procesamiento.resolucion import ResolverNombres
from sistema.procesamiento.seguridad import construir_evento, construir_mensaje_tablet, detectar
from sistema.rag.embeddings import Embeddings
from sistema.rag.esquema import conectar as conectar_rag
from sistema.rag.esquema import crear_esquema
from sistema.rag.fallas import TraductorFallas
from sistema.rag.indexar import indexar
from sistema.reloj import reloj

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s")
log = logging.getLogger("agente")

http = PlantaHTTP()
transcriptor = Transcriptor()
extractor = Extractor()
extractor_cierre = ExtractorCierre()
resolver = ResolverNombres(http)
verificador_rango = VerificadorRango(http)
catalogo_limites = CatalogoLimites(http)
detector_sensores = DetectorSensores(catalogo_limites, http)
embeddings = Embeddings()
traductor_fallas = TraductorFallas(http)

# Lote 6 (B7): el agente se conecta y empieza a latir enseguida; la preparación (parser con el
# catálogo de la planta, Whisper cargado) corre en paralelo. Una nota que llega antes espera esto.
_preparado = threading.Event()


def _urgente(funcion):
    """Marca el trabajo como urgente mientras corre: pausa la indexación de embeddings en segundo
    plano (hallazgo H1, ver `llm/control.py::TrabajoUrgente`) y corta la sugerencia del LLM que
    esté usando Ollama (Lote 6, A3: la sugerencia no se publica, una nota sí apura)."""

    @functools.wraps(funcion)
    def envuelta(*args, **kwargs):
        with trabajo_urgente:
            cancelar_baja_prioridad()
            return funcion(*args, **kwargs)

    return envuelta


# Notas y respuestas del operador en curso (no cierres ni órdenes): los cierres esperan a que esto
# quede en cero antes de usar Whisper (ver `_procesar_cierre_con_menos_prioridad`).
_notas_en_curso = TrabajoUrgente()


def _de_nota(funcion):
    @functools.wraps(funcion)
    def envuelta(*args, **kwargs):
        with _notas_en_curso:
            return funcion(*args, **kwargs)

    return envuelta


# Una nota a la vez (mismo orden que llegan), pero con corte: si una se cuelga (4.1), no se
# bloquea el proceso para siempre — ver `_procesar_nota_con_corte`.
_pool_notas = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nota")

# Pool aparte para el análisis de órdenes (Fase 8/10): comparte el mismo motivo que `_pool_notas`
# (cortar si se cuelga) pero NO puede ser el mismo pool — probado en vivo contra el escenario
# `ataque`: el análisis de una orden tardó 2m29s con el modelo local, y al estar en el mismo
# worker que las notas, dejó NOTA-00001 y NOTA-00007 mudas por 90 s cada una (justo el bug #22 de
# Entrega 1 que este patrón de pool+vigía existe para evitar). Una orden lenta no puede volver a
# frenar una nota, así como una nota lenta no puede frenar el análisis de una orden.
_pool_ordenes = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orden")

# Lote 4: las sugerencias del LLM (que pueden esperar minutos a que no haya notas en curso) van a
# su propio pool, así no dejan en cola a los cierres de `_pool_ordenes`. Con las fallas dichas de
# palabra hay más escalados; las sugerencias no se publican ni puntúan, así que si ya hay
# `MAX_SUGERENCIAS_PENDIENTES` esperando, la nueva se descarta (la orden inmediata sale igual).
_pool_sugerencias = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sugerencia")
MAX_SUGERENCIAS_PENDIENTES = 2
_sugerencias_pendientes = 0
_lock_sugerencias = threading.Lock()

# Lote 6 (B1): las lecturas de sensores se procesan fuera del loop de red de paho-mqtt. Antes
# corrían adentro de `on_message`, con llamadas HTTP (estado de bombas, tendencia del historiador:
# hasta ~31 s con reintentos) y sin try/except: una caída del simulador cortaba `loop_forever` y
# con él todo el agente. Un solo worker conserva el orden de las lecturas. Con tope: si el
# simulador manda más rápido de lo que se procesa, se descartan las que sobran en vez de
# acumular memoria sin límite (la detección mira rachas sostenidas, no cada muestra).
_pool_sensores = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sensor")
MAX_LECTURAS_EN_COLA = 5000
_lecturas_en_cola = 0
_lecturas_descartadas = 0
_lock_lecturas = threading.Lock()

# Diálogos de confirmación de parte en curso, indexados por el id del último MensajeTablet
# enviado (así sabemos a qué pregunta responde cada RespuestaOperador). Caché en memoria del
# proceso para no ir a Postgres en cada mensaje; la fuente de verdad que sobrevive a un reinicio
# es la tabla `dialogos_pendientes` (5.7, ver `persistencia/memoria.py` para la política de qué
# se guarda y por cuánto tiempo) — se recarga en `main()` al arrancar.
dialogos_pendientes: dict[str, EstadoDialogo] = {}

# Lote 6 (B3): la planta publica notas, respuestas y cierres con QoS 1, y nosotros nos suscribimos
# con QoS 1: el broker puede entregar dos veces el mismo mensaje. Una nota repetida no se vuelve a
# procesar (sería otra transcripción, otro diálogo y mensajes duplicados en la tablet).
_notas_recibidas: set[str] = set()
_lock_notas_recibidas = threading.Lock()

# Lote 6 (A6): primer mensaje a la tablet de cada nota (borrador, pregunta, alerta o acuse).
_mensaje_de_nota: dict[str, threading.Event] = {}
_lock_mensaje_de_nota = threading.Lock()


def _evento_mensaje(nota_id: str) -> threading.Event:
    with _lock_mensaje_de_nota:
        return _mensaje_de_nota.setdefault(nota_id, threading.Event())


# ------------------------------------------------------------------------------- publicación

def _publicar(client: mqtt.Client, topico: str, payload: str) -> mqtt.MQTTMessageInfo:
    """Lote 6 (B2): todo lo que publica el sistema va con QoS 1. Si el broker se cae un momento,
    paho guarda el mensaje y lo reenvía al reconectar (con QoS 0 se perdía sin aviso). El broker
    puede entregarlo dos veces: no es problema, cada mensaje tiene id estable y el corrector se
    queda con la primera publicación de cada id."""
    info = client.publish(topico, payload, qos=1)
    if info.rc == mqtt.MQTT_ERR_NO_CONN:
        log.warning("%s: sin conexión con el broker, queda en cola para cuando reconecte", topico)
    elif info.rc != mqtt.MQTT_ERR_SUCCESS:
        log.error("%s: no se pudo publicar (%s)", topico, mqtt.error_string(info.rc))
    return info


def _publicar_tablet(client: mqtt.Client, mensaje: MensajeTablet) -> None:
    _publicar(client, Topicos.TABLET, mensaje.model_dump_json())
    for nota_id in mensaje.nota_ids:
        _evento_mensaje(nota_id).set()


def _confirmado_por_broker(info: mqtt.MQTTMessageInfo, timeout_s: float) -> bool:
    try:
        info.wait_for_publish(timeout=timeout_s)
    except (ValueError, RuntimeError):
        return False
    return info.is_published()


# ------------------------------------------------------------------------------- arranque

def _cargar_dialogos_pendientes() -> None:
    try:
        conn = memoria.conectar()
        try:
            recuperados = memoria.cargar_todos(conn)
        finally:
            conn.close()
    except Exception:
        # Postgres puede no estar listo todavía en el primer arranque del stack (carrera entre
        # contenedores) — no recuperar diálogos viejos no es motivo para no arrancar el agente.
        log.exception("no se pudieron recuperar diálogos pendientes de Postgres al arrancar")
        return
    dialogos_pendientes.update(recuperados)
    for estado in recuperados.values():
        with _lock_notas_recibidas:
            _notas_recibidas.update(estado.parte.nota_ids)
    if recuperados:
        log.info("recuperados %d diálogos pendientes de Postgres tras el arranque", len(recuperados))


def _cargar_ordenes_pendientes() -> None:
    """Fase 12 (F3): al arrancar, loguear cuántas órdenes quedaron con `pendiente_publicar=true`
    de antes del reinicio. No hace falta recuperarlas a memoria del proceso (a diferencia de los
    diálogos): el hilo de reconciliación las vuelve a encontrar solo en su próxima vuelta,
    consultando Postgres directo — la fuente de verdad es la tabla, no una copia en RAM."""
    try:
        conn = persistencia_ordenes.conectar()
        try:
            pendientes = persistencia_ordenes.pendientes_de_publicar(conn)
        finally:
            conn.close()
    except Exception:
        log.exception("no se pudo consultar Postgres por órdenes pendientes al arrancar")
        return
    if pendientes:
        log.info(
            "%d orden(es) quedaron pendientes de publicar de antes del reinicio: %s",
            len(pendientes), [o.id for o in pendientes],
        )


def _memoria_best_effort(accion, *args) -> None:
    """La memoria de conversación (5.7) es una ayuda para sobrevivir un reinicio, no una
    dependencia del camino crítico: si Postgres falla acá, el mensaje a la tablet/el evento ya se
    publicaron igual (se llama DESPUÉS de publicar, nunca antes) — se registra el error y se
    sigue, en vez de perder la nota entera por un problema de persistencia."""
    try:
        conn = memoria.conectar()
        try:
            accion(conn, *args)
        finally:
            conn.close()
    except Exception:
        log.exception("no se pudo actualizar la memoria de conversación en Postgres (nota %s)", args[0])


def _con_reintentos(descripcion: str, funcion, intentos: int = 5, espera_inicial_s: float = 1.0):
    """Lote 6 (B6): para escrituras que no se pueden perder (la orden inmediata, el cierre). Con
    Postgres caído 2 s, antes la orden se perdía para siempre con el evento ya publicado."""
    espera = espera_inicial_s
    for intento in range(1, intentos + 1):
        try:
            return funcion()
        except Exception:
            if intento == intentos:
                raise
            log.warning("%s: falló (intento %d de %d), se reintenta en %.0f s", descripcion, intento, intentos, espera)
            time.sleep(espera)
            espera = min(espera * 2, 8.0)


# ------------------------------------------------------------------------------- notas y diálogo

def _sin_novedad_ya_publicados(texto: str) -> set[str]:
    """Equipos con SinNovedad publicado al llegar la nota (`_escalar_novedades_dichas`, que solo
    corre si la nota no disparó una alerta de seguridad)."""
    if not texto or detectar(texto) is not None:
        return set()
    try:
        return set(equipos_sin_novedad_seguros(texto, resolver.equipos_mencionados))
    except Exception:
        log.exception("no se pudieron recalcular los SinNovedad tempranos")
        return set()


def _avanzar_dialogo(client: mqtt.Client, estado: EstadoDialogo) -> None:
    nota_id = estado.parte.nota_ids[0]
    guardar_parte(nota_id, estado.parte)
    if estado.parte.estado.value == "Confirmado":
        log.info(
            "parte %s confirmado (consumo: %d pasos, %d tokens)",
            estado.parte.id, estado.consumo.pasos, estado.consumo.tokens_totales,
        )
        # Lote 4 (H15): un evento por equipo, escalando también las fallas dichas de palabra.
        try:
            novedades = novedades_del_parte(estado.parte, estado.parte.texto, resolver.equipos_mencionados)
            # Lo que ya se escaló al llegar la nota (L4.9) tiene el mismo id: no puede volver
            # como SinNovedad acá aunque el parte haya atribuido distinto.
            for tag, dichas in novedades_del_texto(estado.parte.texto, resolver.equipos_mencionados).items():
                novedades.setdefault(tag, []).extend(n for n in dichas if n not in novedades[tag])
        except Exception:
            log.exception("parte %s: falló la detección de novedades dichas", estado.parte.id)
            novedades = {}
        eventos = eventos_del_parte(estado.parte, catalogo_limites, http, novedades)
        # L4.12 (H20, E3): el SinNovedad de un equipo que ya salió al llegar la nota (L4.11, otro
        # id) no se repite — para el corrector serían dos eventos iguales de la misma nota. Se
        # recalcula con la misma función sobre el mismo texto (sobrevive a un reinicio).
        ya_sin_novedad = _sin_novedad_ya_publicados(estado.parte.texto)
        eventos = [e for e in eventos if not (e.tipo == TipoEvento.sin_novedad and e.equipo in ya_sin_novedad)]
        for evento in eventos:
            _publicar(client, Topicos.EVENTOS, evento.model_dump_json())
            log.info("sistema/eventos -> %s (%s)", evento.id, evento.tipo.value)
        _memoria_best_effort(memoria.eliminar, nota_id)
        for evento in eventos:
            if evento.tipo == TipoEvento.escalado:
                # Aunque ya estamos en el hilo de `_pool_notas` (llegamos vía `_procesar_nota`/
                # `_procesar_respuesta_en_pool`), el análisis de la orden va a su PROPIO pool+vigía
                # (`_pool_ordenes`) — llamarlo inline acá adentro comparte el único worker de
                # `_pool_notas` con el análisis, y una orden lenta deja mudas a todas las notas
                # siguientes (confirmado en vivo: 2m29s de análisis = dos notas cortadas a los 90 s).
                _procesar_evento_escalado_con_corte(client, evento)
        return
    mensaje = construir_mensaje(estado)
    if detectar_loop(estado, mensaje):
        log.warning("parte %s: loop detectado en el diálogo, se cierra para revisión manual", estado.parte.id)
        _avanzar_dialogo(client, estado)  # ya quedó Confirmado: publica sus eventos y termina
        return
    dialogos_pendientes[mensaje.id] = estado
    _publicar_tablet(client, mensaje)
    log.info("sistema/tablet -> %s: %s", mensaje.id, mensaje.texto)
    _memoria_best_effort(memoria.guardar, nota_id, mensaje.id, estado)


@_urgente
@_de_nota
def _procesar_respuesta_en_pool(client: mqtt.Client, estado: EstadoDialogo, texto_respuesta: str) -> None:
    try:
        procesar_respuesta(estado, texto_respuesta, extractor, resolver, verificador_rango)
        _avanzar_dialogo(client, estado)
    except Exception:
        log.exception("falló el procesamiento de una respuesta del operador (nota %s)", estado.parte.nota_ids)


def _encolar_foto_best_effort(foto_id: str, url: str, orden_id: str | None = None) -> None:
    try:
        encolar_o_analizar(foto_id, url, orden_id=orden_id) if orden_id else encolar_o_analizar(foto_id, url)
    except Exception:
        log.exception("foto %s: falló encolar/analizar", foto_id)


def _fotos_de_nota(nota: NotaRecorrida) -> None:
    """Lote 6 (A5): copia local y cola de visión, después de la alerta y fuera del worker de notas."""
    descargar_fotos(nota, http)
    for i, foto_url in enumerate(nota.fotos):
        _encolar_foto_best_effort(f"{nota.id}-FOTO-{i}", foto_url)


def _escalar_novedades_dichas(client: mqtt.Client, nota: NotaRecorrida, texto: str) -> None:
    """Lote 4 (L4.9): las fallas dichas de palabra escalan apenas se transcribe la nota, sin
    esperar a que el operador confirme el parte (una nota sin números no se confirma nunca con el
    operador simulado). Mismo id que publicará `eventos_del_parte` al confirmar: idempotente."""
    try:
        por_equipo = novedades_del_texto(texto, resolver.equipos_mencionados)
    except Exception:
        log.exception("nota %s: falló la detección temprana de novedades dichas", nota.id)
        return
    for tag, dichas in por_equipo.items():
        evento = EventoSistema(
            id=f"EVT-PARTE-PARTE-{nota.id}-{tag}",
            ts=nota.ts,
            tipo=TipoEvento.escalado,
            equipo=tag,
            origen="parte",
            parte_id=f"PARTE-{nota.id}",
            nota_ids=[nota.id],
            resumen=f"El operador informa en {tag}: " + ", ".join(dichas),
        )
        _publicar(client, Topicos.EVENTOS, evento.model_dump_json())
        log.info("sistema/eventos -> %s (%s, al llegar la nota)", evento.id, evento.tipo.value)
        _procesar_evento_escalado_con_corte(client, evento)

    # L4.11: los equipos nombrados sin ninguna falla dicha quedan SinNovedad ya, aunque el parte
    # tarde (en ráfagas de notas, varias no llegan a confirmarse a tiempo). Id DISTINTO del evento
    # del parte: el corrector se queda con la primera publicación de cada id, así que si al
    # confirmar el parte ese equipo escala por un valor fuera de límite, ese Escalado sale igual
    # con su propio id (para B1 alcanza con que exista).
    try:
        sin_novedad = equipos_sin_novedad_seguros(texto, resolver.equipos_mencionados)
    except Exception:
        log.exception("nota %s: no se pudieron ubicar los equipos nombrados", nota.id)
        return
    for tag in sin_novedad:
        evento = EventoSistema(
            id=f"EVT-NOTA-{nota.id}-{tag}",
            ts=nota.ts,
            tipo=TipoEvento.sin_novedad,
            equipo=tag,
            origen="parte",
            nota_ids=[nota.id],
            resumen=f"{tag}: el operador no informa ninguna falla en la nota (valores a confirmar en el parte).",
        )
        _publicar(client, Topicos.EVENTOS, evento.model_dump_json())
        log.info("sistema/eventos -> %s (%s, al llegar la nota)", evento.id, evento.tipo.value)


@_urgente
@_de_nota
def _procesar_nota(client: mqtt.Client, nota: NotaRecorrida, recibida: float) -> None:
    inicio = time.monotonic()
    if not _preparado.wait(timeout=config.espera_preparacion_s):
        log.warning("nota %s: el agente todavía no terminó de prepararse, se procesa igual", nota.id)
    tiempos: dict[str, float] = {}
    texto = descargar_y_transcribir(nota, http, transcriptor, tiempos)

    # Alerta de seguridad (2.5): chequeo local sobre el texto, antes que nada más (sin
    # depender del LLM de extracción ni del historiador), para entrar dentro de los 30 s
    # reales de la consigna aun si el enlace a internet está caído.
    deteccion = detectar(texto)
    alerta = None
    if deteccion is not None:
        evento = construir_evento(deteccion, nota)
        _publicar(client, Topicos.EVENTOS, evento.model_dump_json())
        mensaje = construir_mensaje_tablet(deteccion, nota)
        _publicar_tablet(client, mensaje)
        alerta = time.monotonic() - recibida
        _marcar_revision_humana(texto)
        log.warning("ALERTA DE SEGURIDAD %s en nota %s: %s", deteccion.codigo, nota.id, deteccion.descripcion)
    else:
        _escalar_novedades_dichas(client, nota, texto)

    # Lote 5b (E2): las fotos de la nota también van a la cola de visión (antes solo las de un
    # cierre). Sin enlace quedan encoladas y el latido las muestra como pendientes. En un hilo
    # aparte: nunca frenan el parte ni la alerta.
    if nota.fotos:
        threading.Thread(target=_fotos_de_nota, args=(nota,), daemon=True, name=f"fotos-{nota.id}").start()

    antes_extraccion = time.monotonic()
    consumo = ConsumoTokens()
    parte = armar_parte(nota, texto, extractor, resolver, verificador_rango, consumo)
    extraccion = time.monotonic() - antes_extraccion
    estado = EstadoDialogo(parte=parte, consumo=consumo)
    _avanzar_dialogo(client, estado)
    fin = time.monotonic()
    # Lote 6 (D1): una línea por nota con dónde se fue el tiempo (`evaluacion/latencias.py`).
    log.info(
        "latencia nota=%s cola=%.1f descarga=%.1f whisper=%.1f audio=%.1f extraccion=%.1f llm_pasos=%d "
        "alerta=%s primer_mensaje=%.1f total=%.1f",
        nota.id, inicio - recibida, tiempos.get("descarga", 0.0), tiempos.get("whisper", 0.0),
        tiempos.get("audio", 0.0), extraccion, consumo.pasos,
        f"{alerta:.1f}" if alerta is not None else "-",
        alerta if alerta is not None else fin - recibida, fin - recibida,
    )


# ------------------------------------------------------------------------------- cierres

def _cargar_orden(orden_id: str):
    conn = persistencia_ordenes.conectar()
    try:
        return persistencia_ordenes.cargar(conn, orden_id)
    finally:
        conn.close()


@_urgente
def _procesar_cierre(client: mqtt.Client, nota_cierre: NotaCierre) -> None:
    """Fase 14.1: una `NotaCierre` mueve la orden a `Ejecutada` (G1). La comparación cierre vs.
    orden (14.2) es best-effort — si transcribir/extraer falla, la orden igual se ejecuta (la
    nota de cierre es la prueba de que el trabajo se hizo; comparar es para detectar diferencias
    a revisar después, no una condición para avanzar el estado).

    Lote 6 (B6): no se tiene una conexión a Postgres abierta mientras corre Whisper (si Postgres se
    reiniciaba en el medio, el cierre se perdía); se lee la orden, se transcribe, y se guarda con
    reintentos."""
    orden = _con_reintentos(f"cierre {nota_cierre.id}: leer la orden", lambda: _cargar_orden(nota_cierre.orden_id))
    if orden is None:
        log.warning("cierre %s: no se encontró la orden %s", nota_cierre.id, nota_cierre.orden_id)
        return
    if orden.estado == EstadoOrden.ejecutada:
        log.info("cierre %s: la orden %s ya estaba Ejecutada (cierre repetido)", nota_cierre.id, orden.id)
        return
    if orden.estado != EstadoOrden.aprobada:
        # Solo se ejecuta lo que se aprobó: un cierre sobre una orden Pendiente, Rechazada o ya
        # cerrada/certificada es una inconsistencia para revisar a mano, no un cambio de estado.
        log.warning(
            "cierre %s: la orden %s está %s (no Aprobada); no se pasa a Ejecutada",
            nota_cierre.id, orden.id, orden.estado.value,
        )
        return

    diferencias: list[str] = []
    try:
        texto = ""
        if nota_cierre.audio_url:
            audio_path = http.descargar(
                nota_cierre.audio_url, config.media_dir / nota_cierre.id / "audio.wav"
            )
            texto = transcriptor.transcribir(audio_path)
        # Parser primero (determinístico, milisegundos); el LLM solo si no reconoce ninguna tarea.
        cierre_extraido = parsear_cierre(texto, [r.codigo for r in orden.repuestos])
        log.info("cierre %s: extraccion=%s", nota_cierre.id, "parser" if cierre_extraido else "llm")
        if cierre_extraido is None:
            cierre_extraido = extractor_cierre.extraer(texto)
        diferencias = comparar_cierre_con_orden(cierre_extraido, orden)
    except Exception:
        log.exception("cierre %s: falló la comparación cierre/orden", nota_cierre.id)

    ejecutada = orden.model_copy(
        update={"estado": EstadoOrden.ejecutada, "motivo": "; ".join(diferencias) or None}
    )

    def _guardar() -> None:
        conn = persistencia_ordenes.conectar()
        try:
            persistencia_ordenes.guardar(conn, ejecutada, pendiente_publicar=True)
        finally:
            conn.close()

    _con_reintentos(f"cierre {nota_cierre.id}: guardar la orden", _guardar)
    log.info(
        "orden %s -> Ejecutada (cierre %s) [correlacion=%s]%s", orden.id, nota_cierre.id,
        persistencia_ordenes.correlacion_id(orden), f", diferencias: {diferencias}" if diferencias else "",
    )


def _procesar_cierre_con_menos_prioridad(client: mqtt.Client, nota_cierre: NotaCierre) -> None:
    """Medido en la corrida `g1-tarea-estandar`: un cierre que llega mientras entran notas compite
    por la CPU (Whisper) con ellas, y las notas se demoran. El cierre no tiene apuro de segundos
    (lo que importa es que la orden llegue a Ejecutada): espera hasta `espera_cierre_s` a que no
    haya ninguna nota ni respuesta del operador en curso, y recién ahí transcribe."""
    esperado = 0.0
    while _notas_en_curso.activo() and esperado < config.espera_cierre_s:
        time.sleep(0.5)
        esperado += 0.5
    if esperado:
        log.info("cierre %s: esperó %.0f s a que terminaran las notas en curso", nota_cierre.id, esperado)
    _procesar_cierre(client, nota_cierre)


def _vigilar(descripcion: str, futuro, arranque: threading.Event, limite_s: float) -> bool:
    """Lote 6 (A6): el corte se cuenta desde que el trabajo **arranca**, no desde que entra a la
    cola (antes, en una ráfaga, una nota que solo esperaba su turno ya recibía "TIMEOUT").
    Devuelve False si se cortó."""
    arranque.wait()
    try:
        futuro.result(timeout=limite_s)
    except FuturoTimeoutError:
        log.error("%s: no terminó en %.0f s desde que arrancó", descripcion, limite_s)
        return False
    except Exception:
        log.exception("%s: falló el procesamiento", descripcion)
    return True


def _enviar_con_arranque(pool: ThreadPoolExecutor, funcion, *args):
    """Manda `funcion` al pool y devuelve (futuro, evento que se prende cuando empieza a correr)."""
    arranque = threading.Event()

    def _envuelta():
        arranque.set()
        return funcion(*args)

    return pool.submit(_envuelta), arranque


def _procesar_cierre_con_corte(client: mqtt.Client, nota_cierre: NotaCierre) -> None:
    futuro, arranque = _enviar_con_arranque(_pool_ordenes, _procesar_cierre_con_menos_prioridad, client, nota_cierre)
    threading.Thread(
        target=_vigilar,
        args=(f"cierre {nota_cierre.id}", futuro, arranque, config.espera_cierre_s + config.timeout_nota_s),
        daemon=True,
    ).start()

    # Fase 13: cualquier foto que venga con el cierre entra a la misma cola/análisis de visión
    # que una foto de un parte — si no hay enlace, queda encolada y se reintenta sola.
    # Lote 6 (B1): en un hilo aparte; antes corría adentro de `on_message` (Postgres + enlace +
    # descarga + visión, hasta minutos con el loop de red de MQTT bloqueado).
    for i, foto_url in enumerate(nota_cierre.fotos):
        threading.Thread(
            target=_encolar_foto_best_effort,
            args=(f"{nota_cierre.id}-FOTO-{i}", foto_url, nota_cierre.orden_id),
            daemon=True,
        ).start()


# ------------------------------------------------------------------------------- órdenes

# Serializa "buscar una orden abierta equivalente + guardar" (E3, sin duplicados): dos escalados
# casi simultáneos del mismo equipo no pueden abrir dos órdenes iguales.
_lock_orden_inmediata = threading.Lock()

# Equipos nombrados en una nota con alerta de seguridad: su orden nunca se aprueba sola, va a
# revisión humana (Anexo D: "si las fotos muestran daño estructural grave, va siempre a revisión
# humana — B-101 en fuga-sin-enlace"). Sin visión configurada, o con el análisis de la foto todavía
# en cola por falta de enlace, el sistema no puede descartar ese daño: la alerta alcanza para no
# aprobar solo. Vive en memoria: tras un reinicio se pierde, pero la orden ya publicada no cambia.
_equipos_revision_humana: set[str] = set()
_lock_revision_humana = threading.Lock()  # Lote 6 (B8): se escribe y se lee desde hilos distintos


def _marcar_revision_humana(texto: str) -> None:
    try:
        equipos = set(resolver.equipos_mencionados(texto))
    except Exception:
        log.exception("no se pudieron identificar los equipos de la alerta de seguridad")
        return
    if equipos:
        with _lock_revision_humana:
            _equipos_revision_humana.update(equipos)
        log.warning("alerta de seguridad: %s quedan con revisión humana obligatoria", sorted(equipos))


def _orden_inmediata_best_effort(evento: EventoSistema) -> None:
    """Lote 3 (Fase 15): la orden que se publica sale de acá, en código y sin LLM, en segundos —
    ver el porqué en `procesamiento/ordenes.py::orden_inmediata`. Se persiste en Postgres con
    `pendiente_publicar=true` (Fase 12) en vez de publicar directo: si el agente se cae justo
    después, el hilo de reconciliación la publica en su próxima vuelta (F3)."""
    with _lock_revision_humana:
        revision_humana = frozenset(_equipos_revision_humana)
    try:
        orden = orden_inmediata(
            evento, http, tarea_estandar=config.ordenes_tarea_estandar, siempre_pendiente=revision_humana,
        )
    except Exception:
        log.exception("evento %s: no se pudo armar la orden inmediata", evento.id)
        return
    if orden is None:
        log.info("evento %s: sin equipo, no genera orden", evento.id)
        return

    def _persistir() -> None:
        with _lock_orden_inmediata:
            conn = persistencia_ordenes.conectar()
            try:
                # Lote 6 (BUGS #63): el mismo evento puede escalar dos veces (al llegar la nota y al
                # confirmarse el parte, mismo id). La orden ya decidida para ese evento no se vuelve
                # a decidir: en `lote6-velocidad` una T-F-CAR Aprobada (la planta ya había mandado
                # al técnico) fue pisada por una I-VAL Pendiente, y el cierre no la pudo ejecutar.
                if persistencia_ordenes.cargar(conn, orden.id) is not None:
                    log.info("evento %s: ya tiene su orden %s, no se vuelve a decidir", evento.id, orden.id)
                    return
                existente = orden_abierta_equivalente(orden, persistencia_ordenes.cargar_todas(conn))
                if existente is not None:
                    actualizada = sumar_evento(existente, evento.id)
                    if actualizada is not existente:
                        persistencia_ordenes.guardar(conn, actualizada, pendiente_publicar=True)
                    log.info("evento %s: se suma a la orden abierta %s (sin duplicar)", evento.id, existente.id)
                    return
                persistencia_ordenes.guardar(conn, orden, pendiente_publicar=True)
            finally:
                conn.close()
        log.info("orden %s (%s) persistida, pendiente de publicar [correlacion=%s]",
                 orden.id, orden.estado.value, persistencia_ordenes.correlacion_id(orden))

    try:
        _con_reintentos(f"orden {orden.id}: persistir", _persistir)
    except Exception:
        log.exception("orden %s: no se pudo persistir en Postgres tras varios intentos", orden.id)


def _sugerencia_llm(client: mqtt.Client, evento: EventoSistema) -> None:
    """Fase 8/10/11 (router fijo: análisis LLM → revisor → reglas), con menos prioridad que las
    notas: espera a que no haya ninguna nota/respuesta/cierre en curso antes de ocupar Ollama. El
    resultado se guarda como sugerencia para el planificador; nunca se publica (ver
    `orden_inmediata`).

    Lote 6 (A3): si llega una nota mientras el análisis corre, la nota corta el pedido a Ollama
    (`_urgente` → `cancelar_baja_prioridad`) y la sugerencia se vuelve a intentar cuando el sistema
    se desocupe (hasta 3 veces). Antes, una vez arrancado, ocupaba Ollama hasta 90 s."""
    for intento in range(1, 4):
        esperado = 0.0
        while trabajo_urgente.activo() and esperado < config.espera_maxima_sugerencia_s:
            time.sleep(1.0)
            esperado += 1.0
        if trabajo_urgente.activo():
            log.info("evento %s: sin sugerencia del LLM (el sistema siguió ocupado con notas)", evento.id)
            return
        generacion = generacion_cancelacion()
        try:
            sugerencia = procesar_evento_escalado(evento, http, embeddings, traductor_fallas)
        except Exception:
            if generacion_cancelacion() != generacion:
                log.info("evento %s: sugerencia cortada por una nota (intento %d)", evento.id, intento)
                continue
            log.exception("evento %s: falló el análisis LLM de la sugerencia", evento.id)
            return
        finally:
            soltar_baja_prioridad()
        if generacion_cancelacion() != generacion:
            # El análisis atrapa sus propios errores y devuelve una inspección: si lo cortó una
            # nota, ese resultado no vale; se reintenta.
            log.info("evento %s: sugerencia cortada por una nota (intento %d), se reintenta", evento.id, intento)
            continue
        break
    else:
        log.info("evento %s: sin sugerencia del LLM (cortada 3 veces por notas)", evento.id)
        return
    if sugerencia is None:
        log.info("evento %s: el análisis no sugiere orden (ver llm/analisis.py)", evento.id)
        return
    try:
        conn = persistencia_ordenes.conectar()
        try:
            persistencia_ordenes.guardar_sugerencia(conn, sugerencia.id, sugerencia)
        finally:
            conn.close()
        log.info("sugerencia del análisis para %s guardada: tareas=%s", sugerencia.id, sugerencia.tareas)
    except Exception:
        log.exception("sugerencia %s: no se pudo persistir", sugerencia.id)


def _procesar_evento_escalado_con_corte(client: mqtt.Client, evento: EventoSistema) -> None:
    """Pool propio, nunca `_pool_notas`: este evento puede llegar del detector de sensores o desde
    `_avanzar_dialogo` (ya en el hilo de `_pool_notas`) — en los dos casos, el análisis de la orden
    no puede competir por el mismo worker que las notas.

    Lote 3: primero la orden inmediata (hilo propio, segundos, sin LLM); la sugerencia del LLM va
    a su pool con menor prioridad."""
    threading.Thread(target=_orden_inmediata_best_effort, args=(evento,), daemon=True).start()
    global _sugerencias_pendientes
    with _lock_sugerencias:
        if _sugerencias_pendientes >= MAX_SUGERENCIAS_PENDIENTES:
            log.info("evento %s: sin sugerencia del LLM (ya hay %d en cola)", evento.id, _sugerencias_pendientes)
            return
        _sugerencias_pendientes += 1

    def _sugerencia_y_descontar() -> None:
        global _sugerencias_pendientes
        try:
            _sugerencia_llm(client, evento)
        finally:
            with _lock_sugerencias:
                _sugerencias_pendientes -= 1

    futuro, arranque = _enviar_con_arranque(_pool_sugerencias, _sugerencia_y_descontar)
    # La sugerencia primero espera a que no haya notas en curso y recién después usa el LLM:
    # el corte cubre las dos cosas.
    limite = config.espera_maxima_sugerencia_s + config.timeout_orden_s
    threading.Thread(target=_vigilar, args=(f"sugerencia de {evento.id}", futuro, arranque, limite), daemon=True).start()


# ------------------------------------------------------------------------------- notas: corte y acuse

def _vigilar_nota(client: mqtt.Client, nota: NotaRecorrida, futuro, arranque: threading.Event) -> None:
    hubo_mensaje = _evento_mensaje(nota.id)
    try:
        # Lote 6 (A6): si a los `acuse_nota_s` no salió nada a la tablet (cola ocupada, Whisper
        # lento), acuse inmediato sin esperar respuesta — el operador nunca queda sin contestación.
        if not hubo_mensaje.wait(timeout=config.acuse_nota_s):
            acuse = MensajeTablet(
                id=f"ACUSE-{nota.id}",
                ts=reloj.ahora(),
                nota_ids=[nota.id],
                texto="Recibí tu nota, la estoy procesando. En un momento te muestro lo que anoté.",
                espera_respuesta=False,
            )
            _publicar_tablet(client, acuse)
            log.info("sistema/tablet -> %s (acuse: la nota todavía no tuvo respuesta)", acuse.id)
        if _vigilar(f"nota {nota.id}", futuro, arranque, config.timeout_nota_s):
            return
        # Ya no se publica un evento `EVT-TIMEOUT` (Escalado sin equipo), hallazgo H18 de la
        # corrida lote4-completa: la nota sigue procesándose en segundo plano y publica sus
        # eventos por equipo al confirmarse; un Escalado sin equipo además "contamina" a todos los
        # equipos sin novedad de esa nota (B1 cuenta cualquier evento sin equipo para cada uno).
        mensaje_fallback = MensajeTablet(
            id=f"TIMEOUT-{nota.id}",
            ts=reloj.ahora(),
            nota_ids=[nota.id],
            texto="Estamos procesando tu nota, puede demorar — seguimos trabajando en segundo plano.",
            espera_respuesta=False,
        )
        _publicar_tablet(client, mensaje_fallback)
    finally:
        # El vigía termina cuando la nota terminó (o se cortó): ya no hace falta recordar su evento.
        if futuro.done():
            with _lock_mensaje_de_nota:
                _mensaje_de_nota.pop(nota.id, None)


def _procesar_nota_con_corte(client: mqtt.Client, nota: NotaRecorrida) -> None:
    """Corte (4.1): si procesar esta nota tarda más que `config.timeout_nota_s`, no la dejamos
    colgada para siempre — avisamos igual en la tablet (nunca quedarse mudo, `sistema_mudo` es
    eliminatorio).

    Importante: esto NO puede esperar (`futuro.result(...)`) en el hilo que llama a `on_message`
    — ese hilo es el loop de red de paho-mqtt, y mientras está bloqueado acá adentro no puede ni
    recibir mensajes nuevos ni *transmitir* los que ya se encolaron (medido en vivo: la alerta de
    seguridad se detectaba a los ~10 s pero no salía hasta ~33 s después, fuera de los 30 s del
    criterio eliminatorio). La espera del corte va en un hilo aparte."""
    futuro, arranque = _enviar_con_arranque(_pool_notas, _procesar_nota, client, nota, time.monotonic())
    threading.Thread(target=_vigilar_nota, args=(client, nota, futuro, arranque), daemon=True,
                     name=f"vigia-{nota.id}").start()


# ------------------------------------------------------------------------------- hilos de fondo

def reconciliar_ordenes(client: mqtt.Client, parar: threading.Event) -> None:
    """Fase 12 (F3): publica en `sistema/ordenes` cualquier orden que haya quedado
    `pendiente_publicar=true` (recién decidida, o decidida a mano vía `interfaces/central.py`).

    Lote 6 (B2): solo se marca publicada si el broker confirmó la recepción (QoS 1) y si la fila no
    cambió mientras tanto. Antes se marcaba siempre: con el broker caído unos segundos, la orden
    quedaba marcada y nunca salía."""
    while not parar.is_set():
        try:
            conn = persistencia_ordenes.conectar()
            try:
                for orden, version in persistencia_ordenes.pendientes_de_publicar_con_version(conn):
                    info = _publicar(client, Topicos.ORDENES, orden.model_dump_json())
                    if not _confirmado_por_broker(info, timeout_s=5.0):
                        log.warning("orden %s: el broker no confirmó la publicación, se reintenta", orden.id)
                        break
                    if persistencia_ordenes.marcar_publicada(conn, orden.id, version):
                        log.info("sistema/ordenes -> %s (%s)", orden.id, orden.estado.value)
                    else:
                        log.info("orden %s cambió mientras se publicaba: sale la versión nueva en la próxima vuelta", orden.id)
            finally:
                conn.close()
        except Exception:
            log.exception("falló la reconciliación de órdenes pendientes de publicar")
        parar.wait(config.intervalo_reconciliacion_s)


def reconciliar_fotos(client: mqtt.Client, parar: threading.Event) -> None:
    """Fase 13.2: reintenta el análisis de visión de las fotos que quedaron encoladas por falta
    de enlace. Hilo propio, nunca el loop de red de paho-mqtt."""
    while not parar.is_set():
        try:
            reconciliar_fotos_pendientes()
        except Exception:
            log.exception("falló la reconciliación de fotos pendientes de análisis")
        parar.wait(config.intervalo_reconciliacion_s)


def _contar_pendientes() -> int | None:
    """Órdenes sin publicar + fotos sin analizar. None si Postgres no responde."""
    try:
        conn = persistencia_ordenes.conectar()
        try:
            ordenes = len(persistencia_ordenes.pendientes_de_publicar(conn))
        finally:
            conn.close()
        conn = persistencia_fotos.conectar()
        try:
            fotos = persistencia_fotos.contar_sin_resolver(conn)
        finally:
            conn.close()
        return ordenes + fotos
    except Exception:
        log.exception("no se pudieron contar los pendientes de sincronizar")
        return None


ARCHIVO_VIVO = "/tmp/agente-vivo"  # lo mira el healthcheck de docker-compose (Lote 6, B9)


def _marcar_vivo() -> None:
    try:
        with open(ARCHIVO_VIVO, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def publicar_estado(client: mqtt.Client, parar: threading.Event) -> None:
    """Latido cada `intervalo_latido_s`, y además **apenas cambia** el enlace o la cantidad de
    pendientes (L4.13, H21): en `fuga-sin-enlace` el corte dura pocos segundos reales.

    Lote 6 (B4): un error en una vuelta ya no mata el hilo (antes no tenía try/except: el latido se
    cortaba para siempre), y si Postgres no responde se informa el último valor conocido de
    pendientes en vez de 0 (un 0 es un falso "todo sincronizado")."""
    ultimo: tuple[bool, int] | None = None
    ultimo_envio = 0.0
    pendientes_conocidos = 0
    while not parar.is_set():
        try:
            enlace_ok = enlace_arriba(timeout=2.0)
            contados = _contar_pendientes()
            if contados is not None:
                pendientes_conocidos = contados
            ahora = time.monotonic()
            if (enlace_ok, pendientes_conocidos) != ultimo or ahora - ultimo_envio >= config.intervalo_latido_s:
                estado = EstadoSistema(
                    ts=reloj.ahora(), sistema="josbert-rizzo", enlace_ok=enlace_ok,
                    pendientes_sincronizar=pendientes_conocidos,
                )
                _publicar(client, Topicos.ESTADO, estado.model_dump_json())
                log.info("sistema/estado -> %s", estado.model_dump_json())
                ultimo, ultimo_envio = (enlace_ok, pendientes_conocidos), ahora
            _marcar_vivo()
        except Exception:
            log.exception("falló una vuelta del latido")
        parar.wait(config.intervalo_sondeo_estado_s)


# ------------------------------------------------------------------------------- MQTT

def on_connect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    log.info("conectado al broker (rc=%s)", reason_code)
    # Se resuscribe en cada (re)conexión: la sesión es limpia (ver `main`).
    client.subscribe("planta/#", qos=1)


def on_disconnect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    # Lote 6 (B3): antes una desconexión no dejaba rastro. paho reconecta solo (reconnect_delay_set)
    # y reenvía lo publicado con QoS 1 que quedó sin confirmar.
    log.warning("desconectado del broker (rc=%s), reconectando...", reason_code)


def _procesar_lectura(client: mqtt.Client, lectura: LecturaSensor) -> None:
    global _lecturas_en_cola
    try:
        evento = detector_sensores.procesar(lectura)
        if evento is not None:
            _publicar(client, Topicos.EVENTOS, evento.model_dump_json())
            log.info("sistema/eventos -> %s (%s)", evento.id, evento.tipo.value)
            if evento.tipo == TipoEvento.escalado:
                _procesar_evento_escalado_con_corte(client, evento)
    except Exception:
        log.exception("falló el procesamiento de la lectura %s/%s", lectura.tag, lectura.variable)
    finally:
        with _lock_lecturas:
            _lecturas_en_cola -= 1


def _encolar_lectura(client: mqtt.Client, lectura: LecturaSensor) -> None:
    global _lecturas_en_cola, _lecturas_descartadas
    with _lock_lecturas:
        if _lecturas_en_cola >= MAX_LECTURAS_EN_COLA:
            _lecturas_descartadas += 1
            if _lecturas_descartadas % 500 == 1:
                log.warning("cola de sensores llena: %d lecturas descartadas", _lecturas_descartadas)
            return
        _lecturas_en_cola += 1
    _pool_sensores.submit(_procesar_lectura, client, lectura)


def _nota_nueva(nota_id: str) -> bool:
    with _lock_notas_recibidas:
        if nota_id in _notas_recibidas:
            return False
        _notas_recibidas.add(nota_id)
        return True


def _on_message(client: mqtt.Client, msg: mqtt.MQTTMessage) -> None:
    if msg.topic.startswith("planta/sensores/"):
        try:
            lectura = LecturaSensor.model_validate_json(msg.payload)
        except Exception:
            log.exception("lectura de sensor inválida en %s", msg.topic)
            return
        reloj.avanzar(lectura.ts)
        _encolar_lectura(client, lectura)
        return

    log.info("recibido %s (%d bytes)", msg.topic, len(msg.payload))

    if msg.topic == Topicos.RECORRIDAS:
        try:
            nota = NotaRecorrida.model_validate_json(msg.payload)
        except Exception:
            log.exception("nota de recorrida inválida en %s", msg.topic)
            return
        reloj.avanzar(nota.ts)
        if not _nota_nueva(nota.id):
            log.info("nota %s repetida (reentrega del broker), se ignora", nota.id)
            return
        # Lote 6 (A3): la nota corta ya la sugerencia del LLM que esté usando Ollama.
        cancelar_baja_prioridad()
        _procesar_nota_con_corte(client, nota)

    elif msg.topic == Topicos.RESPUESTAS:
        try:
            respuesta = RespuestaOperador.model_validate_json(msg.payload)
        except Exception:
            log.exception("respuesta de operador inválida en %s", msg.topic)
            return
        reloj.avanzar(respuesta.ts)
        estado = dialogos_pendientes.pop(respuesta.mensaje_id, None)
        if estado is None:
            log.warning("respuesta %s no corresponde a ningún diálogo pendiente", respuesta.mensaje_id)
            return
        cancelar_baja_prioridad()
        # Igual que con la nota: esto puede llamar al LLM, y hacerlo acá adentro bloquearía el
        # loop de red de paho-mqtt.
        futuro, arranque = _enviar_con_arranque(_pool_notas, _procesar_respuesta_en_pool, client, estado, respuesta.texto)
        threading.Thread(
            target=_vigilar, args=(f"respuesta a {respuesta.mensaje_id}", futuro, arranque, config.timeout_nota_s),
            daemon=True,
        ).start()

    elif msg.topic == Topicos.CIERRES:
        try:
            nota_cierre = NotaCierre.model_validate_json(msg.payload)
        except Exception:
            log.exception("nota de cierre inválida en %s", msg.topic)
            return
        reloj.avanzar(nota_cierre.ts)
        # Mismo pool que el análisis de órdenes (`_pool_ordenes`), nunca `_pool_notas`.
        _procesar_cierre_con_corte(client, nota_cierre)


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    """Lote 6 (B1): nada de lo que pase con un mensaje puede cortar el loop de red de MQTT (paho
    vuelve a lanzar las excepciones de los callbacks y `loop_forever` termina: el agente moría)."""
    try:
        _on_message(client, msg)
    except Exception:
        log.exception("falló el manejo de un mensaje en %s", msg.topic)


# ------------------------------------------------------------------------------- preparación

def _crear_esquema_rag_best_effort() -> None:
    """Solo el DDL (rápido, sin llamar a Ollama): garantiza que `rag_fragmentos` exista ANTES de
    que el agente empiece a recibir eventos (una búsqueda sin filas no rompe nada)."""
    try:
        conn = conectar_rag()
        try:
            crear_esquema(conn)
        finally:
            conn.close()
    except Exception:
        log.exception("no se pudo crear el esquema de rag_fragmentos al arrancar")


def _indexar_corpus_en_segundo_plano() -> None:
    """El análisis de órdenes (Fase 10) depende de `rag_fragmentos` para citar manuales. Indexar en
    un hilo aparte (nunca sincrónico en `main()`): embeber ~700 fragmentos tarda varios minutos en
    esta CPU sin GPU — bloquear el arranque le costó el primer latido en una corrida anterior."""
    try:
        total = indexar(http, embeddings, debe_pausar=trabajo_urgente.activo)
        log.info("corpus RAG indexado en segundo plano: %d fragmentos", total)
    except Exception:
        log.exception("no se pudo indexar el corpus RAG en segundo plano")


def _crear_esquema_fotos_best_effort() -> None:
    """Hallazgo H6 (Fase 15): las tablas se crean en secuencia antes de lanzar los hilos."""
    try:
        persistencia_fotos.conectar().close()
    except Exception:
        log.exception("no se pudo crear el esquema de fotos_pendientes_analisis al arrancar")


def _configurar_parser(parar: threading.Event) -> None:
    """Lote 5: parser determinístico de la cátedra antes del LLM, con el catálogo de
    `GET /api/planta`. Lote 6 (B7): se reintenta en segundo plano hasta que la planta responda (antes
    eran 5 intentos antes de conectar MQTT — hasta ~3 min sin latido — y si fallaban, todo iba por el
    LLM para siempre)."""
    intento = 0
    while not parar.is_set():
        intento += 1
        try:
            configurar_parser(ParserPartes(catalogo_desde_planta(http.equipos())))
            log.info("parser determinístico de notas listo (LLM solo de respaldo)")
            return
        except Exception:
            log.exception("parser determinístico: no se pudo armar el catálogo (intento %d)", intento)
            parar.wait(min(2.0 * intento, 10.0))


def _precalentar_ollama() -> None:
    """Lote 6 (A4): carga el modelo de extracción antes de la primera nota que lo necesite."""
    try:
        inicio = time.monotonic()
        chat_ollama(timeout_s=120.0, num_predict=1).invoke([("human", "ok")])
        log.info("Ollama precalentado en %.1f s", time.monotonic() - inicio)
    except Exception:
        log.warning("no se pudo precalentar Ollama (se carga con la primera nota que lo use)")
    # Lote 7: también el de embeddings (bge-m3 tardó 9 s en la primera consulta en frío).
    try:
        inicio = time.monotonic()
        embeddings.embed_consulta("ok")
        log.info("embeddings precalentados en %.1f s", time.monotonic() - inicio)
    except Exception:
        log.warning("no se pudo precalentar el modelo de embeddings (se carga en la primera búsqueda)")


ESPERA_PARSER_S = 20.0


def _preparar(parar: threading.Event) -> None:
    try:
        # El parser se sigue reintentando en su hilo; la preparación no lo espera para siempre (si
        # la planta tarda, las primeras notas van por el LLM como antes del Lote 5).
        hilo_parser = threading.Thread(target=_configurar_parser, args=(parar,), daemon=True, name="parser")
        hilo_parser.start()
        hilo_parser.join(timeout=ESPERA_PARSER_S)
        _crear_esquema_rag_best_effort()
        _crear_esquema_fotos_best_effort()
        _cargar_ordenes_pendientes()
        try:
            log.info("Whisper precargado en %.1f s", transcriptor.precargar(config.media_dir / "_precarga"))
        except Exception:
            log.exception("no se pudo precargar Whisper (se carga con la primera nota)")
    finally:
        _preparado.set()
    threading.Thread(target=_precalentar_ollama, daemon=True, name="ollama-calentar").start()
    threading.Thread(target=_indexar_corpus_en_segundo_plano, daemon=True, name="indexar").start()


def _conectar_broker(client: mqtt.Client, parar: threading.Event) -> None:
    """Lote 6 (B3): si el broker todavía no está, se reintenta en vez de terminar el proceso."""
    espera = 1.0
    while not parar.is_set():
        try:
            client.connect(config.mqtt_host, config.mqtt_port)
            return
        except OSError as error:
            log.warning("no se pudo conectar al broker (%s), reintento en %.0f s", error, espera)
            parar.wait(espera)
            espera = min(espera * 2, 15.0)


def main() -> None:
    parar = threading.Event()
    # Lote 6 (B9): `docker stop` manda SIGTERM. Antes Python era PID 1 sin handler: se ignoraba y
    # Docker lo mataba a los 10 s.
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())

    # Los diálogos se recuperan antes de suscribirse: una respuesta que llegue al toque tiene que
    # encontrar su diálogo.
    _cargar_dialogos_pendientes()

    # Sesión limpia a propósito: el broker de la planta no se reinicia entre escenarios del
    # corrector, y una sesión persistente traería mensajes de un escenario al siguiente.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=15)
    _conectar_broker(client, parar)
    client.loop_start()

    # Lote 6 (B7): latido enseguida; la preparación corre en paralelo y las notas la esperan.
    for objetivo in (publicar_estado, reconciliar_ordenes, reconciliar_fotos):
        threading.Thread(target=objetivo, args=(client, parar), daemon=True, name=objetivo.__name__).start()
    threading.Thread(target=_preparar, args=(parar,), daemon=True, name="preparar").start()

    parar.wait()
    log.info("apagando: cerrando la conexión con el broker")
    try:
        client.disconnect()
        client.loop_stop()
    except Exception:
        log.exception("error al desconectar del broker")
    for pool in (_pool_notas, _pool_ordenes, _pool_sugerencias, _pool_sensores):
        pool.shutdown(wait=False, cancel_futures=True)
    logging.shutdown()
    # Un Whisper u Ollama colgado en un worker no puede impedir que el proceso termine (los
    # workers no son daemon): Docker reinicia el contenedor solo si el proceso sale.
    os._exit(0)


if __name__ == "__main__":
    main()
