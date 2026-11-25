"""Calibración offline del detector de colmatación acelerada (Lote 9, BUGS #78).

Corre el simulador de la planta EN PROCESO (sin broker ni contenedores, igual que
`planta_sim/app.py` arma los guiones) con la semilla de cada escenario, y cada 30 min de planta
le pasa el historian a `DetectorColmatacion`, igual que el agente. Imprime la primera detección
por filtro y las fallas F07/F08 reales de cada escenario.

Criterio para dejar el detector prendido:
- `sin-luz`: F-201B detectado ANTES del corte de luz (15:30), que es cuando la orden tiene que
  estar pendiente para F3;
- cero detecciones en filtros sin F07 (falsos positivos), también con otras semillas (`--semillas`).

Uso (en el host, con `academia2026-tp` al lado del repo):

    .venv/Scripts/python -m evaluacion.evaluar_colmatacion
    .venv/Scripts/python -m evaluacion.evaluar_colmatacion --semillas 5 --escenarios martes-tranquilo libre
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[2]
ACADEMIA = RAIZ / "academia2026-tp"
sys.path.insert(0, str(ACADEMIA / "planta" / "sim"))

from planta_sim.escenarios import Escenario, listar
from planta_sim.simulador import Simulador

from sistema.procesamiento.colmatacion import Umbrales, detector_desde_planta

PASO = timedelta(minutes=30)


class HistorianDelSimulador:
    """Lo mismo que `PlantaHTTP.historial`, pero contra el simulador en memoria."""

    def __init__(self, sim: Simulador, definicion: dict) -> None:
        self._sim = sim
        self._definicion = definicion

    def equipos(self) -> dict:
        """Lo que sirve `GET /api/planta`: la misma definición de la planta."""
        return self._definicion

    def historial(self, tag: str, variable: str, desde: str, hasta: str) -> list[dict]:
        return self._sim.historial(tag, variable, datetime.fromisoformat(desde), datetime.fromisoformat(hasta))


def correr(ruta: Path, semilla: int | None, umbrales: Umbrales) -> dict:
    datos = ACADEMIA / "datos"
    definicion = yaml.safe_load((datos / "planta.yaml").read_text(encoding="utf-8"))
    materiales = yaml.safe_load((datos / "materiales.yaml").read_text(encoding="utf-8"))
    escenario = Escenario.cargar(ruta)
    if semilla is not None:
        escenario = replace(escenario, semilla=semilla)
    with tempfile.TemporaryDirectory() as media:
        sim = Simulador(definicion, materiales, Path(media), lambda *_: None, lambda *_: None)
        sim.iniciar(escenario)
        # Igual que el agente: filtros, caudalímetro, caudal de diseño y alarmas desde la planta.
        detector = detector_desde_planta(HistorianDelSimulador(sim, definicion), umbrales)
        fin = sim.inicio + (escenario.duracion or timedelta(hours=24))
        t = sim.inicio
        detecciones: list[tuple[datetime, str, str]] = []
        while t < fin:
            t = min(t + PASO, fin)
            sim._correr(t, parar_en_atencion=False)
            for ev in detector.revisar(sim.planta.ahora):
                detecciones.append((sim.planta.ahora, ev.equipo, ev.resumen))
        fallas = [(h["ts"], h.get("codigo"), h.get("equipo")) for h in sim.planta.historia
                  if h["tipo"] == "falla" and h.get("codigo") in ("F07", "F08")]
        cortes = [h["ts"] for h in sim.planta.historia if h["tipo"] == "energia"]
    return {"escenario": escenario.nombre, "semilla": escenario.semilla, "detecciones": detecciones,
            "fallas_filtro": fallas, "cortes": cortes, "inicio": sim.inicio}


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--escenarios", nargs="*", help="por defecto, todos")
    ap.add_argument("--semillas", type=int, default=0, help="semillas extra por escenario (falsos positivos)")
    ap.add_argument("--factor", type=float, default=Umbrales.factor)
    ap.add_argument("--pendiente-min", type=float, default=Umbrales.pendiente_min)
    ap.add_argument("--ventana-h", type=float, default=Umbrales.ventana_h)
    args = ap.parse_args()
    umbrales = Umbrales(factor=args.factor, pendiente_min=args.pendiente_min, ventana_h=args.ventana_h)

    rutas = listar(ACADEMIA / "escenarios")
    nombres = args.escenarios or sorted(rutas)
    falsos, verdaderos, perdidos = 0, 0, 0
    for nombre in nombres:
        base = Escenario.cargar(rutas[nombre]).semilla
        for semilla in [None] + [base + 1000 * (i + 1) for i in range(args.semillas)]:
            t0 = time.monotonic()
            r = correr(rutas[nombre], semilla, umbrales)
            con_falla = {eq for _, cod, eq in r["fallas_filtro"] if cod == "F07"}
            detectados = {eq for _, eq, _ in r["detecciones"]}
            falsos += len(detectados - con_falla)
            verdaderos += len(detectados & con_falla)
            perdidos += len(con_falla - detectados)
            print(f"\n== {r['escenario']} (semilla {r['semilla']}, {time.monotonic() - t0:.0f} s)")
            print(f"   fallas de filtro: {r['fallas_filtro'] or '-'}   cortes de luz: {r['cortes'] or '-'}")
            for ts, eq, resumen in r["detecciones"]:
                marca = "OK " if eq in con_falla else "FALSO"
                print(f"   {marca} {ts.isoformat()} (+{(ts - r['inicio']).total_seconds() / 3600:.1f} h) {resumen}")
            for eq in sorted(con_falla - detectados):
                print(f"   PERDIDO: F07 en {eq} sin detección")
    print(f"\nTotal: {verdaderos} detecciones correctas, {falsos} falsos positivos, {perdidos} F07 sin detectar")


if __name__ == "__main__":
    main()
