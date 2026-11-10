import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturoTimeoutError
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from planta_contrato import (
    EstadoSistema,
    EventoSistema,
    LecturaSensor,
    MensajeTablet,
    NotaRecorrida,
    RespuestaOperador,
    TipoEvento,
    Topicos,
)

from sistema.config import config
from sistema.integraciones.planta_http import PlantaHTTP
from sistema.llm.control import ConsumoTokens
from sistema.llm.extraccion import Extractor
from sistema.llm.transcripcion import Transcriptor
from sistema.persistencia import memoria
from sistema.procesamiento.dialogo import EstadoDialogo, construir_mensaje, procesar_respuesta
from sistema.procesamiento.eventos import DetectorSensores, evaluar_parte
from sistema.procesamiento.limites import CatalogoLimites
from sistema.procesamiento.rango_historico import VerificadorRango
from sistema.procesamiento.recorridas import armar_parte, descargar_y_transcribir, guardar_parte
from sistema.procesamiento.resolucion import ResolverNombres
from sistema.procesamiento.seguridad import construir_evento, construir_mensaje_tablet, detectar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("agente")

http = PlantaHTTP()
transcriptor = Transcriptor()
extractor = Extractor()
resolver = ResolverNombres(http)
verificador_rango = VerificadorRango(http)
catalogo_limites = CatalogoLimites(http)
detector_sensores = DetectorSensores(catalogo_limites, http)

# Una nota a la vez (mismo orden que llegan), pero con corte: si una se cuelga (4.1), no se
# bloquea el proceso para siempre — ver `_procesar_nota_con_corte`.
_pool_notas = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nota")

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


def _avanzar_dialogo(client: mqtt.Client, estado: EstadoDialogo) -> None:
    nota_id = estado.parte.nota_ids[0]
    guardar_parte(nota_id, estado.parte)
    if estado.parte.estado.value == "Confirmado":
        log.info(
            "parte %s confirmado (consumo: %d pasos, %d tokens)",
            estado.parte.id, estado.consumo.pasos, estado.consumo.tokens_totales,
        )
        evento = evaluar_parte(estado.parte, catalogo_limites, http)
        client.publish(Topicos.EVENTOS, evento.model_dump_json())
        log.info("sistema/eventos -> %s (%s)", evento.id, evento.tipo.value)
        _memoria_best_effort(memoria.eliminar, nota_id)
        return
    mensaje = construir_mensaje(estado)
    dialogos_pendientes[mensaje.id] = estado
    client.publish(Topicos.TABLET, mensaje.model_dump_json())
    log.info("sistema/tablet -> %s: %s", mensaje.id, mensaje.texto)
    _memoria_best_effort(memoria.guardar, nota_id, mensaje.id, estado)


def _procesar_respuesta_en_pool(client: mqtt.Client, estado: EstadoDialogo, texto_respuesta: str) -> None:
    try:
        procesar_respuesta(estado, texto_respuesta, extractor, resolver, verificador_rango)
        _avanzar_dialogo(client, estado)
    except Exception:
        log.exception("falló el procesamiento de una respuesta del operador")


def _procesar_nota(client: mqtt.Client, nota: NotaRecorrida) -> None:
    texto = descargar_y_transcribir(nota, http, transcriptor)

    # Alerta de seguridad (2.5): chequeo local sobre el texto, antes que nada más (sin
    # depender del LLM de extracción ni del historiador), para entrar dentro de los 30 s
    # reales de la consigna aun si el enlace a internet está caído.
    deteccion = detectar(texto)
    if deteccion is not None:
        evento = construir_evento(deteccion, nota)
        client.publish(Topicos.EVENTOS, evento.model_dump_json())
        mensaje = construir_mensaje_tablet(deteccion, nota)
        client.publish(Topicos.TABLET, mensaje.model_dump_json())
        log.warning("ALERTA DE SEGURIDAD %s en nota %s: %s", deteccion.codigo, nota.id, deteccion.descripcion)

    consumo = ConsumoTokens()
    parte = armar_parte(nota, texto, extractor, resolver, verificador_rango, consumo)
    estado = EstadoDialogo(parte=parte, consumo=consumo)
    _avanzar_dialogo(client, estado)


def _vigilar_corte(client: mqtt.Client, nota: NotaRecorrida, futuro) -> None:
    try:
        futuro.result(timeout=config.timeout_nota_s)
    except FuturoTimeoutError:
        log.error("nota %s: no se terminó de procesar en %.0f s, se corta", nota.id, config.timeout_nota_s)
        evento = EventoSistema(
            id=f"EVT-TIMEOUT-{nota.id}",
            ts=datetime.now(timezone.utc),
            tipo=TipoEvento.escalado,
            origen="parte",
            nota_ids=[nota.id],
            resumen=f"La nota {nota.id} no se terminó de procesar a tiempo (> {config.timeout_nota_s:.0f} s): revisar a mano.",
        )
        client.publish(Topicos.EVENTOS, evento.model_dump_json())
        mensaje_fallback = MensajeTablet(
            id=f"TIMEOUT-{nota.id}",
            ts=datetime.now(timezone.utc),
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


def publicar_estado(client: mqtt.Client) -> None:
    while True:
        estado = EstadoSistema(
            ts=datetime.now(timezone.utc),
            sistema="josbert-rizzo",
            enlace_ok=True,
            pendientes_sincronizar=0,
        )
        client.publish(Topicos.ESTADO, estado.model_dump_json())
        log.info("sistema/estado -> %s", estado.model_dump_json())
        time.sleep(30)


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
        evento = detector_sensores.procesar(lectura)
        if evento is not None:
            client.publish(Topicos.EVENTOS, evento.model_dump_json())
            log.info("sistema/eventos -> %s (%s)", evento.id, evento.tipo.value)
        return

    log.info("recibido %s (%d bytes)", msg.topic, len(msg.payload))

    if msg.topic == Topicos.RECORRIDAS:
        try:
            nota = NotaRecorrida.model_validate_json(msg.payload)
        except Exception:
            log.exception("nota de recorrida inválida en %s", msg.topic)
            return
        _procesar_nota_con_corte(client, nota)

    elif msg.topic == Topicos.RESPUESTAS:
        try:
            respuesta = RespuestaOperador.model_validate_json(msg.payload)
        except Exception:
            log.exception("respuesta de operador inválida en %s", msg.topic)
            return
        estado = dialogos_pendientes.pop(respuesta.mensaje_id, None)
        if estado is None:
            log.warning("respuesta %s no corresponde a ningún diálogo pendiente", respuesta.mensaje_id)
            return
        # Igual que con la nota (ver `_procesar_nota_con_corte`): esto puede llamar al LLM
        # (`procesar_respuesta`), y hacerlo acá adentro de `on_message` bloquearía el loop de red
        # de paho-mqtt, retrasando la transmisión de cualquier mensaje ya encolado.
        _pool_notas.submit(_procesar_respuesta_en_pool, client, estado, respuesta.texto)


def main() -> None:
    _cargar_dialogos_pendientes()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(config.mqtt_host, config.mqtt_port)

    threading.Thread(target=publicar_estado, args=(client,), daemon=True).start()
    client.loop_forever()


if __name__ == "__main__":
    main()
