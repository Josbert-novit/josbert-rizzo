import functools
import logging
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
from sistema.llm.control import ConsumoTokens, TrabajoUrgente, analisis_orden_en_curso, trabajo_urgente
from sistema.llm.extraccion import Extractor
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
from sistema.procesamiento.recorridas import armar_parte, descargar_y_transcribir, guardar_parte
from sistema.procesamiento.resolucion import ResolverNombres
from sistema.procesamiento.seguridad import construir_evento, construir_mensaje_tablet, detectar
from sistema.rag.embeddings import Embeddings
from sistema.rag.esquema import conectar as conectar_rag
from sistema.rag.esquema import crear_esquema
from sistema.rag.fallas import TraductorFallas
from sistema.rag.indexar import indexar
from sistema.reloj import reloj

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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


def _urgente(funcion):
    """Marca el trabajo como urgente mientras corre: pausa la indexación de embeddings en segundo
    plano (hallazgo H1, ver `llm/control.py::TrabajoUrgente`)."""

    @functools.wraps(funcion)
    def envuelta(*args, **kwargs):
        with trabajo_urgente:
            return funcion(*args, **kwargs)

    return envuelta


# Notas y respuestas del operador en curso (no cierres ni órdenes): los cierres esperan a que esto
# quede en cero antes de usar Whisper (ver `_procesar_cierre_con_menos_prioridad`).
_notas_en_curso = TrabajoUrgente()
ESPERA_CIERRE_S = 120.0


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

# Diálogos de confirmación de parte en curso, indexados por el id del último MensajeTablet
# enviado (así sabemos a qué pregunta responde cada RespuestaOperador). Caché en memoria del
# proceso para no ir a Postgres en cada mensaje; la fuente de verdad que sobrevive a un reinicio
# es la tabla `dialogos_pendientes` (5.7, ver `persistencia/memoria.py` para la política de qué
# se guarda y por cuánto tiempo) — se recarga en `main()` al arrancar.
dialogos_pendientes: dict[str, EstadoDialogo] = {}


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
            client.publish(Topicos.EVENTOS, evento.model_dump_json())
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
    client.publish(Topicos.TABLET, mensaje.model_dump_json())
    log.info("sistema/tablet -> %s: %s", mensaje.id, mensaje.texto)
    _memoria_best_effort(memoria.guardar, nota_id, mensaje.id, estado)


@_urgente
@_de_nota
def _procesar_respuesta_en_pool(client: mqtt.Client, estado: EstadoDialogo, texto_respuesta: str) -> None:
    try:
        procesar_respuesta(estado, texto_respuesta, extractor, resolver, verificador_rango)
        _avanzar_dialogo(client, estado)
    except Exception:
        log.exception("falló el procesamiento de una respuesta del operador")


def _encolar_foto_best_effort(foto_id: str, url: str) -> None:
    try:
        encolar_o_analizar(foto_id, url)
    except Exception:
        log.exception("foto %s: falló encolar/analizar", foto_id)


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
        client.publish(Topicos.EVENTOS, evento.model_dump_json())
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
        client.publish(Topicos.EVENTOS, evento.model_dump_json())
        log.info("sistema/eventos -> %s (%s, al llegar la nota)", evento.id, evento.tipo.value)


@_urgente
@_de_nota
def _procesar_nota(client: mqtt.Client, nota: NotaRecorrida) -> None:
    texto = descargar_y_transcribir(nota, http, transcriptor)

    # Alerta de seguridad (2.5): chequeo local sobre el texto, antes que nada más (sin
    # depender del LLM de extracción ni del historiador), para entrar dentro de los 30 s
    # reales de la consigna aun si el enlace a internet está caído.
    deteccion = detectar(texto)
    if deteccion is not None:
        evento = construir_evento(deteccion, nota)
        client.publish(Topicos.EVENTOS, evento.model_dump_json())
        _marcar_revision_humana(texto)
        mensaje = construir_mensaje_tablet(deteccion, nota)
        client.publish(Topicos.TABLET, mensaje.model_dump_json())
        log.warning("ALERTA DE SEGURIDAD %s en nota %s: %s", deteccion.codigo, nota.id, deteccion.descripcion)
    else:
        _escalar_novedades_dichas(client, nota, texto)

    # Lote 5b (E2): las fotos de la nota también van a la cola de visión (antes solo las de un
    # cierre). Sin enlace quedan encoladas y el latido las muestra como pendientes. En un hilo
    # aparte: nunca frenan el parte ni la alerta.
    for i, foto_url in enumerate(nota.fotos):
        threading.Thread(target=_encolar_foto_best_effort, args=(f"{nota.id}-FOTO-{i}", foto_url), daemon=True).start()

    consumo = ConsumoTokens()
    parte = armar_parte(nota, texto, extractor, resolver, verificador_rango, consumo)
    estado = EstadoDialogo(parte=parte, consumo=consumo)
    _avanzar_dialogo(client, estado)


@_urgente
def _procesar_cierre(client: mqtt.Client, nota_cierre: NotaCierre) -> None:
    """Fase 14.1: una `NotaCierre` mueve la orden a `Ejecutada` (G1). La comparación cierre vs.
    orden (14.2) es best-effort — si transcribir/extraer falla, la orden igual se ejecuta (la
    nota de cierre es la prueba de que el trabajo se hizo; comparar es para detectar diferencias
    a revisar después, no una condición para avanzar el estado)."""
    conn = persistencia_ordenes.conectar()
    try:
        orden = persistencia_ordenes.cargar(conn, nota_cierre.orden_id)
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
        persistencia_ordenes.guardar(conn, ejecutada, pendiente_publicar=True)
        log.info(
            "orden %s -> Ejecutada (cierre %s) [correlacion=%s]%s", orden.id, nota_cierre.id,
            persistencia_ordenes.correlacion_id(orden), f", diferencias: {diferencias}" if diferencias else "",
        )
    finally:
        conn.close()


def _procesar_cierre_con_menos_prioridad(client: mqtt.Client, nota_cierre: NotaCierre) -> None:
    """Medido en la corrida `g1-tarea-estandar`: un cierre que llega mientras entran notas compite
    por la CPU (Whisper) con ellas, y las notas se demoran. El cierre no tiene apuro de segundos
    (lo que importa es que la orden llegue a Ejecutada): espera hasta ESPERA_CIERRE_S a que no haya
    ninguna nota ni respuesta del operador en curso, y recién ahí transcribe."""
    esperado = 0.0
    while _notas_en_curso.activo() and esperado < ESPERA_CIERRE_S:
        time.sleep(0.5)
        esperado += 0.5
    if esperado:
        log.info("cierre %s: esperó %.0f s a que terminaran las notas en curso", nota_cierre.id, esperado)
    _procesar_cierre(client, nota_cierre)


def _vigilar_corte_cierre(nota_cierre: NotaCierre, futuro) -> None:
    limite = ESPERA_CIERRE_S + config.timeout_nota_s
    try:
        futuro.result(timeout=limite)
    except FuturoTimeoutError:
        log.error("cierre %s: no terminó de procesarse en %.0f s", nota_cierre.id, limite)
    except Exception:
        log.exception("cierre %s: falló el procesamiento", nota_cierre.id)


def _procesar_cierre_con_corte(client: mqtt.Client, nota_cierre: NotaCierre) -> None:
    futuro = _pool_ordenes.submit(_procesar_cierre_con_menos_prioridad, client, nota_cierre)
    threading.Thread(target=_vigilar_corte_cierre, args=(nota_cierre, futuro), daemon=True).start()

    # Fase 13: cualquier foto que venga con el cierre entra a la misma cola/análisis de visión
    # que una foto de un parte — si no hay enlace, queda encolada y se reintenta sola.
    for i, foto_url in enumerate(nota_cierre.fotos):
        try:
            encolar_o_analizar(f"{nota_cierre.id}-FOTO-{i}", foto_url, orden_id=nota_cierre.orden_id)
        except Exception:
            log.exception("cierre %s: falló encolar/analizar la foto %d", nota_cierre.id, i)


# Serializa "buscar una orden abierta equivalente + guardar" (E3, sin duplicados): dos escalados
# casi simultáneos del mismo equipo no pueden abrir dos órdenes iguales.
_lock_orden_inmediata = threading.Lock()

# Equipos nombrados en una nota con alerta de seguridad: su orden nunca se aprueba sola, va a
# revisión humana (Anexo D: "si las fotos muestran daño estructural grave, va siempre a revisión
# humana — B-101 en fuga-sin-enlace"). Sin visión configurada, o con el análisis de la foto todavía
# en cola por falta de enlace, el sistema no puede descartar ese daño: la alerta alcanza para no
# aprobar solo. Vive en memoria: tras un reinicio se pierde, pero la orden ya publicada no cambia.
_equipos_revision_humana: set[str] = set()


def _marcar_revision_humana(texto: str) -> None:
    try:
        equipos = set(resolver.equipos_mencionados(texto))
    except Exception:
        log.exception("no se pudieron identificar los equipos de la alerta de seguridad")
        return
    if equipos:
        _equipos_revision_humana.update(equipos)
        log.warning("alerta de seguridad: %s quedan con revisión humana obligatoria", sorted(equipos))


def _orden_inmediata_best_effort(evento: EventoSistema) -> None:
    """Lote 3 (Fase 15): la orden que se publica sale de acá, en código y sin LLM, en segundos —
    ver el porqué en `procesamiento/ordenes.py::orden_inmediata`. Se persiste en Postgres con
    `pendiente_publicar=true` (Fase 12) en vez de publicar directo: si el agente se cae justo
    después, el hilo de reconciliación la publica en su próxima vuelta (F3)."""
    try:
        orden = orden_inmediata(
            evento, http, tarea_estandar=config.ordenes_tarea_estandar,
            siempre_pendiente=frozenset(_equipos_revision_humana),
        )
    except Exception:
        log.exception("evento %s: no se pudo armar la orden inmediata", evento.id)
        return
    if orden is None:
        log.info("evento %s: sin equipo, no genera orden", evento.id)
        return
    try:
        with _lock_orden_inmediata:
            conn = persistencia_ordenes.conectar()
            try:
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
    except Exception:
        log.exception("orden %s: no se pudo persistir en Postgres", orden.id)


ESPERA_MAXIMA_SUGERENCIA_S = 600.0


def _sugerencia_llm(client: mqtt.Client, evento: EventoSistema) -> None:
    """Fase 8/10/11 (router fijo: análisis LLM → revisor → reglas), ahora con menos prioridad que
    las notas: espera a que no haya ninguna nota/respuesta/cierre en curso antes de ocupar Ollama
    (en esta CPU una llamada de análisis tarda minutos, y Ollama atiende de a una). El resultado
    se guarda como sugerencia para el planificador; nunca se publica (ver `orden_inmediata`)."""
    esperado = 0.0
    while trabajo_urgente.activo() and esperado < ESPERA_MAXIMA_SUGERENCIA_S:
        time.sleep(1.0)
        esperado += 1.0
    if trabajo_urgente.activo():
        log.info("evento %s: sin sugerencia del LLM (el sistema siguió ocupado con notas)", evento.id)
        return
    analisis_orden_en_curso.set()
    try:
        with trabajo_urgente:  # pausa la indexación de embeddings mientras usa Ollama
            sugerencia = procesar_evento_escalado(evento, http, embeddings, traductor_fallas)
    except Exception:
        log.exception("evento %s: falló el análisis LLM de la sugerencia", evento.id)
        return
    finally:
        analisis_orden_en_curso.clear()
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


def _vigilar_corte_orden(evento: EventoSistema, futuro) -> None:
    # La sugerencia primero espera a que no haya notas en curso (hasta ESPERA_MAXIMA_SUGERENCIA_S)
    # y recién después usa el LLM (hasta timeout_orden_s): el corte del vigía cubre las dos.
    limite = ESPERA_MAXIMA_SUGERENCIA_S + config.timeout_orden_s
    try:
        futuro.result(timeout=limite)
    except FuturoTimeoutError:
        log.error("evento %s: la sugerencia del análisis no terminó en %.0f s", evento.id, limite)
    except Exception:
        log.exception("evento %s: falló el análisis de la orden", evento.id)


def _procesar_evento_escalado_con_corte(client: mqtt.Client, evento: EventoSistema) -> None:
    """Pool propio (`_pool_ordenes`), nunca `_pool_notas`: este evento puede llegar directo de
    `DetectorSensores` dentro de `on_message` (el hilo de red de paho-mqtt, que no puede esperar
    sincrónicamente) o desde `_avanzar_dialogo` (ya en el hilo de `_pool_notas`) — en los dos
    casos, el análisis de la orden no puede competir por el mismo worker que las notas.

    Lote 3: primero la orden inmediata (hilo propio, segundos, sin LLM); la sugerencia del LLM va
    al pool de órdenes con menor prioridad."""
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

    futuro = _pool_sugerencias.submit(_sugerencia_y_descontar)
    threading.Thread(target=_vigilar_corte_orden, args=(evento, futuro), daemon=True).start()


def _vigilar_corte(client: mqtt.Client, nota: NotaRecorrida, futuro) -> None:
    try:
        futuro.result(timeout=config.timeout_nota_s)
    except FuturoTimeoutError:
        log.error("nota %s: no se terminó de procesar en %.0f s, se corta", nota.id, config.timeout_nota_s)
        # Ya no se publica un evento `EVT-TIMEOUT` (Escalado sin equipo), hallazgo H18 de la
        # corrida lote4-completa: la nota sigue procesándose en segundo plano y publica sus
        # eventos por equipo al confirmarse; un Escalado sin equipo además "contamina" a todos los
        # equipos sin novedad de esa nota (B1 cuenta cualquier evento sin equipo para cada uno).
        # Lo que no puede faltar es la respuesta en la tablet (`sistema_mudo` es eliminatorio).
        mensaje_fallback = MensajeTablet(
            id=f"TIMEOUT-{nota.id}",
            ts=reloj.ahora(),
            nota_ids=[nota.id],
            texto="Estamos procesando tu nota, puede demorar — seguimos trabajando en segundo plano.",
            espera_respuesta=False,
        )
        client.publish(Topicos.TABLET, mensaje_fallback.model_dump_json())
    except Exception:
        log.exception("nota %s: falló el procesamiento", nota.id)


def _procesar_nota_con_corte(client: mqtt.Client, nota: NotaRecorrida) -> None:
    """Corte (4.1): si procesar esta nota tarda más que `config.timeout_nota_s`, no la dejamos
    colgada para siempre — avisamos igual en la tablet (nunca quedarse mudo, `sistema_mudo` es
    eliminatorio) y publicamos un evento para que quede visible que necesita revisión humana.

    Importante: esto NO puede esperar (`futuro.result(...)`) en el hilo que llama a `on_message`
    — ese hilo es el mismo loop de red de paho-mqtt (`client.loop_forever()`), y mientras está
    bloqueado acá adentro no puede ni recibir mensajes nuevos ni, más grave, *transmitir* los que
    ya se encolaron con `client.publish(...)` (quedan en el buffer de salida hasta que el loop
    vuelve a correr). Medido en vivo contra el corrector: la alerta de seguridad (S1) se detectaba
    y se encolaba a los ~10 s, pero no se transmitía de verdad hasta que esta nota terminaba de
    procesarse del todo ~33 s después — fuera de los 30 s reales que exige el criterio
    eliminatorio `sin_alerta_de_seguridad`. La espera del corte se mueve a un hilo aparte para que
    `on_message` vuelva enseguida."""
    futuro = _pool_notas.submit(_procesar_nota, client, nota)
    threading.Thread(target=_vigilar_corte, args=(client, nota, futuro), daemon=True).start()


INTERVALO_RECONCILIACION_ORDENES_S = 5.0  # academia2026-tp/docs/operacion-entrega.md


def reconciliar_ordenes(client: mqtt.Client) -> None:
    """Fase 12 (F3): cada `INTERVALO_RECONCILIACION_ORDENES_S`, publica en `sistema/ordenes`
    cualquier orden que haya quedado `pendiente_publicar=true` (recién analizada, o recién
    decidida a mano vía `interfaces/central.py`) y la marca publicada. Corre en su propio hilo,
    nunca en el loop de red de paho-mqtt."""
    while True:
        try:
            conn = persistencia_ordenes.conectar()
            try:
                pendientes = persistencia_ordenes.pendientes_de_publicar(conn)
                for orden in pendientes:
                    client.publish(Topicos.ORDENES, orden.model_dump_json())
                    persistencia_ordenes.marcar_publicada(conn, orden.id)
                    log.info("sistema/ordenes -> %s (%s)", orden.id, orden.estado.value)
            finally:
                conn.close()
        except Exception:
            log.exception("falló la reconciliación de órdenes pendientes de publicar")
        time.sleep(INTERVALO_RECONCILIACION_ORDENES_S)


def _ordenes_pendientes_de_publicar_best_effort() -> int:
    try:
        conn = persistencia_ordenes.conectar()
        try:
            return len(persistencia_ordenes.pendientes_de_publicar(conn))
        finally:
            conn.close()
    except Exception:
        return 0


def _fotos_pendientes_de_analisis_best_effort() -> int:
    try:
        conn = persistencia_fotos.conectar()
        try:
            return persistencia_fotos.contar_sin_resolver(conn)
        finally:
            conn.close()
    except Exception:
        return 0


def reconciliar_fotos(client: mqtt.Client) -> None:
    """Fase 13.2: reintenta el análisis de visión de las fotos que quedaron encoladas por falta
    de enlace. Mismo intervalo y mismo motivo que `reconciliar_ordenes` (hilo propio, nunca el
    loop de red de paho-mqtt) — acá además depende de `enlace_arriba()`, así que la mayoría de
    las vueltas no hacen nada mientras el enlace sigue caído."""
    while True:
        try:
            reconciliar_fotos_pendientes()
        except Exception:
            log.exception("falló la reconciliación de fotos pendientes de análisis")
        time.sleep(INTERVALO_RECONCILIACION_ORDENES_S)


INTERVALO_LATIDO_S = 30.0
INTERVALO_SONDEO_ESTADO_S = 2.0


def publicar_estado(client: mqtt.Client) -> None:
    """Latido cada `INTERVALO_LATIDO_S`, y además **apenas cambia** el enlace o la cantidad de
    pendientes (L4.13, H21): en `fuga-sin-enlace` el corte dura pocos segundos reales (el reloj
    simulado corre rápido) y con un latido fijo cada 30 s ninguno caía dentro del corte, así que
    el corrector nunca veía `enlace_ok=false` con pendientes > 0 (E2), aunque la foto de la nota
    sí había quedado en cola."""
    ultimo: tuple[bool, int] | None = None
    ultimo_envio = 0.0
    while True:
        enlace_ok = enlace_arriba(timeout=2.0)
        # Entrega 1 lo dejó en 0 fijo ("es de la Entrega 2: bloque E"); ahora es real: órdenes
        # sin publicar (Fase 12) + fotos encoladas sin analizar por falta de enlace (Fase 13).
        pendientes = _ordenes_pendientes_de_publicar_best_effort() + _fotos_pendientes_de_analisis_best_effort()
        ahora = time.monotonic()
        if (enlace_ok, pendientes) != ultimo or ahora - ultimo_envio >= INTERVALO_LATIDO_S:
            estado = EstadoSistema(
                ts=reloj.ahora(), sistema="josbert-rizzo", enlace_ok=enlace_ok, pendientes_sincronizar=pendientes
            )
            client.publish(Topicos.ESTADO, estado.model_dump_json())
            log.info("sistema/estado -> %s", estado.model_dump_json())
            ultimo, ultimo_envio = (enlace_ok, pendientes), ahora
        time.sleep(INTERVALO_SONDEO_ESTADO_S)


def on_connect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    log.info("conectado al broker (rc=%s)", reason_code)
    client.subscribe("planta/#")


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    if msg.topic.startswith("planta/sensores/"):
        try:
            lectura = LecturaSensor.model_validate_json(msg.payload)
        except Exception:
            log.exception("lectura de sensor inválida en %s", msg.topic)
            return
        reloj.avanzar(lectura.ts)
        evento = detector_sensores.procesar(lectura)
        if evento is not None:
            client.publish(Topicos.EVENTOS, evento.model_dump_json())
            log.info("sistema/eventos -> %s (%s)", evento.id, evento.tipo.value)
            if evento.tipo == TipoEvento.escalado:
                _procesar_evento_escalado_con_corte(client, evento)
        return

    log.info("recibido %s (%d bytes)", msg.topic, len(msg.payload))

    if msg.topic == Topicos.RECORRIDAS:
        try:
            nota = NotaRecorrida.model_validate_json(msg.payload)
        except Exception:
            log.exception("nota de recorrida inválida en %s", msg.topic)
            return
        reloj.avanzar(nota.ts)
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
        # Igual que con la nota (ver `_procesar_nota_con_corte`): esto puede llamar al LLM
        # (`procesar_respuesta`), y hacerlo acá adentro de `on_message` bloquearía el loop de red
        # de paho-mqtt, retrasando la transmisión de cualquier mensaje ya encolado.
        _pool_notas.submit(_procesar_respuesta_en_pool, client, estado, respuesta.texto)

    elif msg.topic == Topicos.CIERRES:
        try:
            nota_cierre = NotaCierre.model_validate_json(msg.payload)
        except Exception:
            log.exception("nota de cierre inválida en %s", msg.topic)
            return
        reloj.avanzar(nota_cierre.ts)
        # Mismo pool que el análisis de órdenes (`_pool_ordenes`), nunca `_pool_notas`: transcribir
        # y comparar llama a Whisper/Ollama, y es tan "lento" como el análisis — no puede competir
        # por el mismo worker que las notas (ver nota de `_pool_ordenes` más arriba).
        _procesar_cierre_con_corte(client, nota_cierre)


def _crear_esquema_rag_best_effort() -> None:
    """Solo el DDL (rápido, sin llamar a Ollama): garantiza que `rag_fragmentos` exista ANTES de
    que el agente empiece a recibir eventos, para que un escalado que llegue mientras el corpus
    todavía se está indexando (ver `_indexar_corpus_en_segundo_plano`) encuentre la tabla vacía
    en vez de un `UndefinedTable` — una búsqueda sin filas no rompe nada (el revisor, Fase 11,
    simplemente no encuentra ninguna cita vigente y cae a la tarea de inspección)."""
    try:
        conn = conectar_rag()
        try:
            crear_esquema(conn)
        finally:
            conn.close()
    except Exception:
        log.exception("no se pudo crear el esquema de rag_fragmentos al arrancar")


def _indexar_corpus_en_segundo_plano() -> None:
    """El análisis de órdenes (Fase 10) depende de `rag_fragmentos` para citar manuales —
    hallazgo en vivo corriendo el corrector real: con los volúmenes recién creados (el corrector
    hace `down -v` entre escenarios), la tabla no existía y el análisis de cada orden fallaba con
    `UndefinedTable`. Indexar en un hilo aparte (nunca sincrónico en `main()`, como se probó
    primero): embeber ~700 fragmentos tarda varios minutos en esta CPU sin GPU — bloquear el
    arranque hasta terminar le costó el primer latido del sistema (`sistema/estado`) en las 4
    escenarios de la corrida siguiente, contra los 180 s reales que el corrector espera antes de
    soltar el reloj (`sistema_mudo`, eliminatorio). Mismo patrón que cualquier otro trabajo lento:
    nunca bloquear lo que tiene que responder rápido."""
    try:
        total = indexar(http, embeddings, debe_pausar=trabajo_urgente.activo)
        log.info("corpus RAG indexado en segundo plano: %d fragmentos", total)
    except Exception:
        log.exception("no se pudo indexar el corpus RAG en segundo plano")


def _crear_esquema_fotos_best_effort() -> None:
    """Hallazgo H6 (Fase 15): cada `conectar()` de persistencia corre su `CREATE TABLE IF NOT
    EXISTS`, y si dos hilos lo hacen a la vez sobre una base recién creada, Postgres choca en el
    catálogo (`duplicate key ... pg_type_typname_nsp_index`). Las demás tablas ya se crean en
    orden acá en `main()` (RAG, diálogos, órdenes); faltaba la de fotos, que recién se creaba
    desde el hilo `reconciliar_fotos` en paralelo con la indexación."""
    try:
        persistencia_fotos.conectar().close()
    except Exception:
        log.exception("no se pudo crear el esquema de fotos_pendientes_analisis al arrancar")


def _configurar_parser_best_effort() -> None:
    """Lote 5: parser determinístico de la cátedra antes del LLM, con el catálogo de
    `GET /api/planta`. Si la planta todavía no responde, se reintenta unas veces; si no, todo
    sigue por el LLM (como antes del Lote 5) en vez de no arrancar."""
    for intento in range(5):
        try:
            configurar_parser(ParserPartes(catalogo_desde_planta(http.equipos())))
            log.info("parser determinístico de notas listo (LLM solo de respaldo)")
            return
        except Exception:
            log.exception("parser determinístico: no se pudo armar el catálogo (intento %d)", intento + 1)
            time.sleep(2.0)
    log.error("parser determinístico no disponible: las notas van todas por el LLM")


def main() -> None:
    # Todo el DDL en secuencia, antes de lanzar cualquier hilo (H6).
    _configurar_parser_best_effort()
    _crear_esquema_rag_best_effort()
    _crear_esquema_fotos_best_effort()
    _cargar_dialogos_pendientes()
    _cargar_ordenes_pendientes()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(config.mqtt_host, config.mqtt_port)

    threading.Thread(target=publicar_estado, args=(client,), daemon=True).start()
    threading.Thread(target=reconciliar_ordenes, args=(client,), daemon=True).start()
    threading.Thread(target=reconciliar_fotos, args=(client,), daemon=True).start()
    threading.Thread(target=_indexar_corpus_en_segundo_plano, daemon=True).start()
    client.loop_forever()


if __name__ == "__main__":
    main()
