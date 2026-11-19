"""Verificación en vivo de la Entrega 2 (Bloque 3 del PLAN.md): un guion, una sola sesión.

Contra el stack levantado (planta + esta solución), recorre lo que el corrector oficial no
ejercita o ejercita poco, y lo que el anexo pide que se revise a mano:

  1. humo: la central responde y el latido trae `pendientes_sincronizar`;
  2. persistencia + publicación: órdenes sembradas en Postgres salen por `sistema/ordenes`;
  3. correlation ID y repuestos con disponibilidad/costo en la central;
  4. apagado de golpe (`docker compose kill` del agente y Postgres) → vuelven con el mismo id y
     el mismo estado, sin duplicarse;
  5. transiciones del planificador/supervisor/inspector por HTTP (incluidos los 409/422);
  6. cierre por MQTT (`planta/cierres`) → `Ejecutada`; cierre sobre una orden NO aprobada → no cambia;
  7. certificación de la contratista con el monto de la lista CT-SVP-2025;
  8. enlace caído → foto en cola → `pendientes_sincronizar > 0` → vuelve el enlace → 0.

Las órdenes de prueba usan el prefijo `ORD-VERIF-` y se borran al final (`--no-limpiar` para
dejarlas). Uso, desde `josbert-rizzo/` con el stack arriba:

    .venv/Scripts/python -m evaluacion.verificar_entrega2
    .venv/Scripts/python -m evaluacion.verificar_entrega2 --sin-kill   # sin el apagado de golpe
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import paho.mqtt.client as mqtt

REPO = Path(__file__).resolve().parents[1]
PREFIJO = "ORD-VERIF-"


class Registro:
    def __init__(self) -> None:
        self.resultados: list[tuple[str, bool, str]] = []

    def check(self, nombre: str, ok: bool, detalle: str = "") -> bool:
        self.resultados.append((nombre, ok, detalle))
        print(f"  [{'OK ' if ok else 'MAL'}] {nombre}{' — ' + detalle if detalle else ''}", flush=True)
        return ok

    def resumen(self) -> int:
        malos = [r for r in self.resultados if not r[1]]
        print(f"\n{len(self.resultados) - len(malos)}/{len(self.resultados)} chequeos OK")
        for nombre, _, detalle in malos:
            print(f"  - FALLA: {nombre} {detalle}")
        return 1 if malos else 0


class Escucha:
    """Junta todo lo que el sistema publica en `sistema/#`."""

    def __init__(self, host: str, puerto: int) -> None:
        self.mensajes: list[tuple[float, str, dict]] = []
        self._lock = threading.Lock()
        self.cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"verificar-{uuid.uuid4().hex[:6]}")
        self.cliente.on_connect = lambda c, *_: c.subscribe("sistema/#", qos=1)
        self.cliente.on_message = self._al_recibir
        self.cliente.connect(host, puerto)
        self.cliente.loop_start()

    def _al_recibir(self, _c, _u, msg) -> None:
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            return
        with self._lock:
            self.mensajes.append((time.time(), msg.topic, payload))

    def esperar(self, topico: str, condicion, timeout: float, desde: float = 0.0) -> dict | None:
        limite = time.time() + timeout
        while time.time() < limite:
            with self._lock:
                for t, top, p in self.mensajes:
                    if t >= desde and top == topico and condicion(p):
                        return p
            time.sleep(0.5)
        return None

    def publicar(self, topico: str, payload: dict) -> None:
        self.cliente.publish(topico, json.dumps(payload, ensure_ascii=False), qos=1).wait_for_publish(5)


def compose(*args: str, timeout: float = 180) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=REPO, capture_output=True, text=True, timeout=timeout)


def exec_agente(codigo: str) -> str:
    r = compose("exec", "-T", "agente", "python", "-c", codigo, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"exec en el agente falló: {r.stderr.strip()[-500:]}")
    return r.stdout


def sembrar(orden: dict) -> None:
    exec_agente(
        "import json\n"
        "from planta_contrato import OrdenSistema\n"
        "from sistema.persistencia import ordenes as po\n"
        f"o = OrdenSistema.model_validate(json.loads({json.dumps(json.dumps(orden))}))\n"
        "c = po.conectar(); po.guardar(c, o, pendiente_publicar=True); c.close()\n"
    )


def limpiar() -> None:
    exec_agente(
        "from sistema.persistencia import ordenes as po\n"
        f"c = po.conectar(); c.execute(\"DELETE FROM ordenes WHERE id LIKE '{PREFIJO}%'\"); c.close()\n"
        "from sistema.persistencia import fotos as pf\n"
        "c = pf.conectar(); c.execute(\"DELETE FROM fotos_pendientes_analisis WHERE id LIKE 'VERIF-%'\"); c.close()\n"
    )


def _orden(sufijo: str, equipo: str, tareas: list[str], ejecutor: str, repuestos: list[dict]) -> dict:
    return {
        "id": f"{PREFIJO}{sufijo}", "ts": datetime.now(timezone.utc).isoformat(), "estado": "PendienteAprobacion",
        "equipo": equipo, "evento_ids": [f"EVT-VERIF-{sufijo}"], "tareas": tareas, "detiene_equipo": False,
        "reduce_inyeccion": False, "ejecutor": ejecutor, "repuestos": repuestos,
        "motivo": "orden sembrada por verificar_entrega2",
    }


def _nota_cierre(orden_id: str, empresa: str) -> dict:
    return {"id": f"CIERRE-VERIF-{uuid.uuid4().hex[:6]}", "ts": datetime.now(timezone.utc).isoformat(),
            "orden_id": orden_id, "tecnico": "Verificación", "empresa": empresa, "audio_url": None, "fotos": []}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--central", default="http://localhost:8200")
    ap.add_argument("--planta", default="http://localhost:8100")
    ap.add_argument("--mqtt-host", default="localhost")
    ap.add_argument("--mqtt-puerto", type=int, default=1883)
    ap.add_argument("--sin-kill", action="store_true", help="no hacer el apagado de golpe")
    ap.add_argument("--no-limpiar", action="store_true")
    args = ap.parse_args()

    reg = Registro()
    central = httpx.Client(base_url=args.central, timeout=15)
    planta = httpx.Client(base_url=args.planta, timeout=15)
    escucha = Escucha(args.mqtt_host, args.mqtt_puerto)
    propia, contra, pend = f"{PREFIJO}PROPIA", f"{PREFIJO}CONTRA", f"{PREFIJO}PEND"

    def orden(oid: str) -> dict | None:
        r = central.get(f"/api/ordenes/{oid}")
        return r.json() if r.status_code == 200 else None

    def publicada(oid: str, estado: str, timeout: float = 20, desde: float = 0.0) -> bool:
        return escucha.esperar("sistema/ordenes", lambda p: p.get("id") == oid and p.get("estado") == estado,
                               timeout, desde) is not None

    try:
        print("1. Humo")
        reg.check("central responde GET /api/ordenes", central.get("/api/ordenes").status_code == 200)
        latido = escucha.esperar("sistema/estado", lambda p: "pendientes_sincronizar" in p, 40)
        reg.check("latido en sistema/estado con pendientes_sincronizar", latido is not None, str(latido))

        print("2. Persistencia y publicación")
        limpiar()
        t0 = time.time()
        sembrar(_orden("PROPIA", "B-102", ["T-B-EMP"], "propio", [{"codigo": "TX-EMP-KIT", "cantidad": 1}]))
        sembrar(_orden("CONTRA", "B-101", ["I-VIB"], "contratista", []))
        sembrar(_orden("PEND", "B-103", ["T-B-EMP"], "propio", [{"codigo": "TX-ROD-CIG", "cantidad": 1}]))
        for oid in (propia, contra, pend):
            reg.check(f"{oid} publicada en sistema/ordenes", publicada(oid, "PendienteAprobacion", 20, t0))

        print("3. Correlation ID y repuestos")
        o = orden(propia) or {}
        reg.check("correlacion_id = evento raíz", o.get("correlacion_id") == "EVT-VERIF-PROPIA", str(o.get("correlacion_id")))
        det = {r["codigo"]: r for r in o.get("repuestos_detalle", [])}
        reg.check("repuesto con disponibilidad y costo unitario",
                  det.get("TX-EMP-KIT", {}).get("disponibilidad") == "yacimiento"
                  and det["TX-EMP-KIT"].get("costo_unitario_usd") == 420, str(det))
        det_pend = {r["codigo"]: r for r in (orden(pend) or {}).get("repuestos_detalle", [])}
        reg.check("TX-ROD-CIG figura disponible solo en central",
                  det_pend.get("TX-ROD-CIG", {}).get("disponibilidad") == "central", str(det_pend))

        if not args.sin_kill:
            print("4. Apagado de golpe (kill de agente y postgres, como el corte de luz de sin-luz)")
            r = compose("kill", "agente", "postgres")
            reg.check("docker compose kill", r.returncode == 0, r.stderr.strip()[-200:])
            r = compose("up", "-d", "postgres", "agente", timeout=300)
            reg.check("docker compose up -d", r.returncode == 0, r.stderr.strip()[-200:])
            t1 = time.time()
            latido = escucha.esperar("sistema/estado", lambda p: True, 180, t1)
            reg.check("el agente vuelve a latir", latido is not None)
            for oid in (propia, contra, pend):
                o = None
                for _ in range(20):
                    try:
                        o = orden(oid)
                        break
                    except httpx.HTTPError:
                        time.sleep(3)
                reg.check(f"{oid} sobrevive con el mismo id y estado",
                          bool(o) and o["estado"] == "PendienteAprobacion", str(o and o["estado"]))
            ids = [x["id"] for x in central.get("/api/ordenes").json() if x["id"].startswith(PREFIJO)]
            reg.check("sin duplicados tras el reinicio", sorted(ids) == sorted({propia, contra, pend}), str(ids))

        print("5. Transiciones por HTTP")
        reg.check("rechazar sin motivo → 422", central.post(f"/api/ordenes/{propia}/rechazar", json={}).status_code == 422)
        reg.check("cerrar una Pendiente → 409", central.post(f"/api/ordenes/{propia}/cerrar").status_code == 409)
        t2 = time.time()
        for oid in (propia, contra):
            r = central.post(f"/api/ordenes/{oid}/aprobar")
            reg.check(f"aprobar {oid}", r.status_code == 200 and r.json()["estado"] == "Aprobada", r.text[:200])
            reg.check(f"{oid} Aprobada publicada", publicada(oid, "Aprobada", 20, t2))
        reg.check("aprobar dos veces → 409", central.post(f"/api/ordenes/{propia}/aprobar").status_code == 409)

        print("6. Cierre por MQTT")
        t3 = time.time()
        escucha.publicar("planta/cierres", _nota_cierre(propia, "propio"))
        escucha.publicar("planta/cierres", _nota_cierre(contra, "Servipat"))
        escucha.publicar("planta/cierres", _nota_cierre(pend, "propio"))
        for oid in (propia, contra):
            reg.check(f"{oid} pasa a Ejecutada", publicada(oid, "Ejecutada", 90, t3))
        o = orden(propia) or {}
        reg.check("el cierre marca diferencias con la orden", bool(o.get("motivo")), str(o.get("motivo")))
        time.sleep(5)
        reg.check("cierre sobre una orden NO aprobada no la ejecuta", (orden(pend) or {}).get("estado") == "PendienteAprobacion")

        print("7. Cierre del supervisor y certificación del inspector")
        r = central.post(f"/api/ordenes/{propia}/cerrar")
        reg.check("supervisor cierra trabajo propio → Cerrada", r.status_code == 200 and r.json()["estado"] == "Cerrada", r.text[:200])
        reg.check("cerrar trabajo de contratista → 409", central.post(f"/api/ordenes/{contra}/cerrar").status_code == 409)
        r = central.post(f"/api/ordenes/{contra}/certificar", json={"habilita": "HAB-2026-9999"})
        cuerpo = r.json() if r.status_code == 200 else {}
        reg.check("inspector certifica → Certificada", cuerpo.get("estado") == "Certificada", r.text[:300])
        reg.check("monto del habilita desde CT-SVP-2025 (I-VIB 320 x 3 = 960)", "960" in r.text, r.text[:300])

        print("8. Enlace caído → foto en cola")
        planta.post("/api/control/accion", json={"accion": "enlace", "params": {"estado": "caido"}})
        time.sleep(3)
        exec_agente(
            "from sistema.procesamiento.fotos import encolar_o_analizar\n"
            "print(encolar_o_analizar('VERIF-FOTO-1', '/data/no-existe.jpg'))\n"
        )
        t4 = time.time()
        con_pend = escucha.esperar("sistema/estado", lambda p: p.get("pendientes_sincronizar", 0) > 0 and not p.get("enlace_ok"), 40, t4)
        reg.check("sin enlace: latido avisa pendientes > 0", con_pend is not None, str(con_pend))
        planta.post("/api/control/accion", json={"accion": "enlace", "params": {"estado": "arriba"}})
        t5 = time.time()
        vuelve = escucha.esperar("sistema/estado", lambda p: p.get("enlace_ok") and p.get("pendientes_sincronizar", 1) == 0, 90, t5)
        reg.check("vuelve el enlace: la cola se vacía", vuelve is not None, str(vuelve))
    except Exception as e:  # noqa: BLE001 — el guion sigue reportando lo que alcanzó a medir
        reg.check("el guion terminó sin excepciones", False, f"{type(e).__name__}: {e}")
    finally:
        planta.post("/api/control/accion", json={"accion": "enlace", "params": {"estado": "arriba"}})
        if not args.no_limpiar:
            try:
                limpiar()
            except Exception as e:  # noqa: BLE001
                print(f"(no se pudo limpiar: {e})")
        escucha.cliente.loop_stop()

    return reg.resumen()


if __name__ == "__main__":
    sys.exit(main())
