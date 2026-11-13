import socket
import threading
from datetime import datetime, timedelta, timezone

from sistema.integraciones import enlace
from sistema.reloj import RelojPlanta


def test_reloj_usa_el_ts_simulado_y_no_retrocede():
    r = RelojPlanta()
    t = datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc)
    r.avanzar(t)
    r.avanzar(t - timedelta(minutes=5))  # mensaje atrasado de otro tópico
    assert r.ahora() == t
    r.avanzar(t + timedelta(hours=1))
    assert r.ahora() == t + timedelta(hours=1)


def test_reloj_sin_mensajes_cae_al_reloj_de_la_maquina():
    antes = datetime.now(timezone.utc)
    assert RelojPlanta().ahora() >= antes


def _proxy_falso(responde: bool) -> int:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def atender():
        conn, _ = srv.accept()
        conn.recv(1024)
        if responde:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        conn.close()  # caído: cierra sin responder, igual que planta/enlace/enlace.py
        srv.close()

    threading.Thread(target=atender, daemon=True).start()
    return srv.getsockname()[1]


def test_enlace_arriba_si_el_proxy_responde(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{_proxy_falso(responde=True)}")
    assert enlace.enlace_arriba(timeout=2) is True


def test_enlace_caido_si_el_proxy_cierra_sin_responder(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{_proxy_falso(responde=False)}")
    assert enlace.enlace_arriba(timeout=2) is False


def test_enlace_caido_si_no_hay_proxy_escuchando(monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{puerto}")
    assert enlace.enlace_arriba(timeout=1) is False
