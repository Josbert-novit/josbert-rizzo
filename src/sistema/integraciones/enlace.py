"""Estado real del enlace a internet, para el latido (`EstadoSistema.enlace_ok`).

El único camino de la planta a internet es el proxy `enlace:3128`. Cuando el enlace se cae, el
proxy cierra las conexiones nuevas sin responder nada; cuando está arriba, contesta algo (aunque
sea un 502 si el destino no responde). Así que alcanza con pedirle un CONNECT y ver si responde:
no hace falta que haya internet de verdad del otro lado, ni depender de un sitio externo.
"""

import os
import socket
from urllib.parse import urlsplit

DESTINO_SONDEO = "example.com:443"


def _proxy() -> tuple[str, int] | None:
    url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if not url:
        return None
    u = urlsplit(url)
    return u.hostname or "enlace", u.port or 3128


def enlace_arriba(timeout: float = 5.0) -> bool:
    proxy = _proxy()
    if proxy is None:
        # Sin proxy configurado (correr fuera de la planta, tests): salida directa, se asume arriba.
        return True
    try:
        with socket.create_connection(proxy, timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"CONNECT {DESTINO_SONDEO} HTTP/1.1\r\nHost: {DESTINO_SONDEO}\r\n\r\n".encode())
            try:
                respuesta = s.recv(64)
            except socket.timeout:
                # El proxy aceptó y está intentando salir (latencia del enlace): no lo cerró, está arriba.
                return True
            return respuesta.startswith(b"HTTP/")
    except OSError:
        return False
