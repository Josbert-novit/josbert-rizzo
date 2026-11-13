"""Reloj de la planta (contrato, "El tiempo"): la planta tiene su propio reloj simulado y corre
acelerada, así que los `ts` que publicamos tienen que salir de ahí y no de `datetime.now()`.

Se toma el `ts` del último mensaje recibido de la planta (las lecturas de sensores llegan seguido,
así que va al día). Solo si todavía no llegó ninguno (recién arrancado) se cae al reloj de la
máquina, para no bloquear un mensaje por eso.
"""

import threading
from datetime import datetime, timezone


class RelojPlanta:
    def __init__(self) -> None:
        self._ultimo: datetime | None = None
        self._lock = threading.Lock()

    def avanzar(self, ts: datetime) -> None:
        """Registra el `ts` simulado de un mensaje recibido. Nunca retrocede (los mensajes pueden
        llegar desordenados entre tópicos)."""
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        with self._lock:
            if self._ultimo is None or ts > self._ultimo:
                self._ultimo = ts

    def ahora(self) -> datetime:
        with self._lock:
            return self._ultimo if self._ultimo is not None else datetime.now(timezone.utc)


reloj = RelojPlanta()
