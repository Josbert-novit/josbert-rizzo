"""Medición offline, ANTES de activarla, de la tarea estándar por evidencia clara (G1).

Toma las corridas guardadas del corrector (`academia2026-tp/correccion/corridas/*/<escenario>.json`)
y, para cada orden publicada, recalcula qué habría propuesto `orden_inmediata(tarea_estandar=True)`
con el MISMO evento y el MISMO estado de la planta que el corrector guardó en el momento de la
decisión. Después juzga esa orden hipotética con las funciones del propio corrector
(`puntuar.py::reglas`, `TAREAS[...]["resuelve"]`):

- eliminatoria: la aprobaríamos y el corrector dice que viola alguna regla (`aprobacion_indebida`);
- D1 perdido: quedaría Pendiente y el corrector esperaba Aprobada;
- D2: fallas reales del escenario que dejarían de tener una orden adecuada (o que pasarían a tenerla);
- G1 potencial: órdenes que saldrían Aprobada (la planta manda un cierre de esas).

Uso (desde josbert-rizzo/):
    .venv/Scripts/python -m evaluacion.evaluar_tarea_estandar            # todas las corridas
    .venv/Scripts/python -m evaluacion.evaluar_tarea_estandar lote5c-completa -v
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import yaml
from planta_contrato import EventoSistema, OrdenSistema

from sistema.integraciones import materiales
from sistema.procesamiento.ordenes import _tarea_del_catalogo, tarea_estandar_clara
from sistema.procesamiento.reglas_aprobacion import evaluar_reglas, hay_reserva_disponible

RAIZ_PLANTA = Path(__file__).resolve().parents[2] / "academia2026-tp"
CORRIDAS = RAIZ_PLANTA / "correccion" / "corridas"


def _cargar_puntuar():
    spec = importlib.util.spec_from_file_location("puntuar", RAIZ_PLANTA / "correccion" / "puntuar.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["puntuar"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


class _HttpFoto:
    """`http.estado()` con la foto de la planta que guardó el corrector al decidir."""

    def __init__(self, bombas: dict[str, str]) -> None:
        self._bombas = bombas

    def estado(self) -> dict:
        return {"bombas": [{"tag": t, "estado": e} for t, e in self._bombas.items()]}


def _hipotetica(orden: dict, codigo: str) -> dict:
    tarea = _tarea_del_catalogo(codigo, orden["equipo"])
    return orden | {"tareas": [codigo], "repuestos": list(tarea.get("repuestos", [])),
                    "detiene_equipo": bool(tarea.get("detiene", True)), "ejecutor": tarea.get("ejecutor", "propio")}


def evaluar(corridas: list[str], verbose: bool) -> int:
    pt = _cargar_puntuar()
    rubrica = yaml.safe_load((RAIZ_PLANTA / "correccion" / "rubrica.yaml").read_text(encoding="utf-8"))
    params = pt._param_reglas(rubrica)
    tot = Counter()
    hallazgos: list[str] = []

    for carpeta in sorted(CORRIDAS.iterdir()):
        if corridas and carpeta.name not in corridas:
            continue
        for archivo in sorted(carpeta.glob("*.json")):
            if archivo.name in ("resultado.json", "meta.json"):
                continue
            datos = json.loads(archivo.read_text(encoding="utf-8"))
            if "registro" not in datos:
                continue
            esc = archivo.stem
            c = pt.Corrida(esc, datos)
            siempre = frozenset()  # el agente en vivo no conoce la rúbrica: solo la foto puede forzar Pendiente
            reemplazos: dict[str, dict] = {}

            for oid, pubs in c.ordenes.items():
                d = pt.decision(pubs)
                if d is None:
                    continue
                tot["ordenes"] += 1
                evento_id = d["payload"]["evento_ids"][0]
                evento_reg = c.eventos.get(evento_id)
                if evento_reg is None:
                    continue
                evento = EventoSistema.model_validate(evento_reg["payload"])
                codigo = tarea_estandar_clara(evento)
                if codigo is None:
                    continue
                tot["con_tarea_estandar"] += 1
                hip = _hipotetica(d["payload"], codigo)
                bombas = (d.get("planta") or {}).get("bombas", {})
                http = _HttpFoto(bombas)
                reserva = hay_reserva_disponible(hip["equipo"], http) if hip["detiene_equipo"] else True
                motivos = evaluar_reglas(OrdenSistema.model_validate(hip | {"estado": "Borrador"}), materiales,
                                         reserva, False, costo_max_usd=params.get("costo_max_usd", 2500),
                                         almacen=params.get("almacen", "yacimiento"), siempre_pendiente=siempre)
                nuestro = "Aprobada" if not motivos else "PendienteAprobacion"
                fallan = pt.reglas({"payload": hip, "planta": d.get("planta")}, esc, params)
                tot[f"tarea_{codigo}"] += 1
                if nuestro == "Aprobada":
                    tot["aprobadas"] += 1
                if nuestro == "Aprobada" and fallan:
                    tot["ELIMINATORIA"] += 1
                    hallazgos.append(f"ELIMINATORIA {carpeta.name}/{esc} {oid}: {codigo} viola {fallan}")
                elif nuestro == "PendienteAprobacion" and not fallan:
                    tot["d1_perdido"] += 1
                    hallazgos.append(f"D1 {carpeta.name}/{esc} {oid}: {codigo} Pendiente {motivos} pero el corrector la aprobaba")
                else:
                    tot["d1_coincide"] += 1
                if verbose:
                    print(f"  {carpeta.name}/{esc} {oid}: {codigo} → {nuestro} (corrector: {fallan or 'aprueba'}) "
                          f"bombas={bombas}")
                reemplazos[oid] = hip

            # D2 antes y después, con la primera publicación de cada orden (como el corrector).
            ignoradas = set(params.get("fallas_ignoradas", [])) | set(
                rubrica["bloques"]["D"]["criterios"]["D2"].get("parametros", {}).get("fallas_ignoradas", []))
            inspeccion = set(rubrica["bloques"]["D"]["criterios"]["D2"].get("parametros", {}).get("tareas_de_inspeccion", []))
            for f in [e for e in datos["historia"] if e["tipo"] == "falla" and e.get("codigo") not in ignoradas]:
                desde = pt.ts(f["ts"])
                sirve = {t for t, dd in pt.TAREAS.items() if f["codigo"] in dd["resuelve"]} | inspeccion

                def hay(con_reemplazos: bool) -> bool:
                    for oid, pubs in c.ordenes.items():
                        o = pubs[0]
                        payload = reemplazos.get(oid, o["payload"]) if con_reemplazos else o["payload"]
                        if payload["equipo"] == f["equipo"] and sirve & set(payload["tareas"]) and pt.ts(o["ts_sim"]) >= desde:
                            return True
                    return False

                antes, despues = hay(False), hay(True)
                tot["fallas"] += 1
                if antes and not despues:
                    tot["d2_perdido"] += 1
                    hallazgos.append(f"D2 perdido {carpeta.name}/{esc}: falla {f['codigo']} en {f['equipo']}")
                if despues and not antes:
                    tot["d2_ganado"] += 1

    print("\nResumen:", dict(tot))
    for h in hallazgos:
        print(" ", h)
    return 1 if tot["ELIMINATORIA"] else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corridas", nargs="*")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    return evaluar(a.corridas, a.v)


if __name__ == "__main__":
    sys.exit(main())
