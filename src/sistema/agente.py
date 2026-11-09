import json
import logging
import threading
import time

import paho.mqtt.client as mqtt

from sistema.config import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("agente")


def publicar_estado(client: mqtt.Client) -> None:
    while True:
        estado = {
            "sistema": "josbert-rizzo",
            "enlace_ok": True,
            "pendientes_sincronizar": 0,
        }
        client.publish("sistema/estado", json.dumps(estado))
        log.info("sistema/estado -> %s", estado)
        time.sleep(30)


def on_connect(client: mqtt.Client, userdata, flags, reason_code, properties=None) -> None:
    log.info("conectado al broker (rc=%s)", reason_code)
    client.subscribe("planta/#")


def on_message(client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
    log.info("recibido %s (%d bytes)", msg.topic, len(msg.payload))


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(config.mqtt_host, config.mqtt_port)

    threading.Thread(target=publicar_estado, args=(client,), daemon=True).start()
    client.loop_forever()


if __name__ == "__main__":
    main()
