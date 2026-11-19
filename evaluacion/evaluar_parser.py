"""Evaluación offline del parte por voz SIN LLM (Lote 5), sobre transcripciones reales.

Toma las corridas del corrector (`academia2026-tp/correccion/corridas/<corrida>/`): de cada
escenario, la transcripción real de Whisper (del log del agente) y la verdad de campo (del JSON).
Arma el borrador con `ParserPartes` (parser de la cátedra + mañas de Whisper) y lo compara con la
misma función que usa el operador simulado para decidir si confirma
(`planta_sim/dialogo.py::_comparar`). Corre en segundos, sin Docker ni modelos.

    python -m evaluacion.evaluar_parser                     # todas las corridas
    python -m evaluacion.evaluar_parser lote4b-completa -v  # una, con el detalle de los fallos

Cuenta por nota: borrador perfecto sin preguntar, con ambigüedad (corresponde preguntar), va al
LLM (el parser dejó texto sin entender) y falla.
"""

import argparse
import glob
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ACADEMIA = RAIZ / "academia2026-tp"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("corridas", nargs="*", help="nombres de carpeta en correccion/corridas (por defecto, todas)")
    ap.add_argument("--academia", type=Path, default=ACADEMIA)
    ap.add_argument("-v", "--detalle", action="store_true")
    args = ap.parse_args()

    sys.path.insert(0, str(args.academia / "planta" / "sim"))
    import yaml
    from planta_sim.dialogo import _comparar

    from sistema.procesamiento.parser_notas import ParserPartes, catalogo_desde_planta

    planta = yaml.safe_load((args.academia / "datos" / "planta.yaml").read_text(encoding="utf-8"))
    parser = ParserPartes(catalogo_desde_planta(planta))
    base = args.academia / "correccion" / "corridas"
    carpetas = args.corridas or sorted(p.name for p in base.iterdir() if p.is_dir())

    total = perfectas = ambiguas = a_llm = 0
    fallos = []
    for carpeta in carpetas:
        for archivo in sorted(glob.glob(str(base / carpeta / "*.json"))):
            if archivo.endswith(("resultado.json", "meta.json")):
                continue
            log = Path(archivo).with_suffix(".log")
            if not log.exists():
                continue
            datos = json.loads(Path(archivo).read_text(encoding="utf-8"))
            transcripciones = dict(re.findall(r"nota (\S+): audio=\S+ fotos=\d+ transcripcion='(.*?)' filas=",
                                              log.read_text(encoding="utf-8", errors="replace")))
            for nota in datos.get("verdad", []):
                if not isinstance(nota, dict) or "items" not in nota or nota.get("seguridad"):
                    continue
                texto = transcripciones.get(nota["nota_id"])
                if texto is None:
                    continue
                total += 1
                filas = parser.parsear(texto, datetime.now(timezone.utc), nota.get("fotos") or [])
                if filas is None:
                    a_llm += 1
                    continue
                if any(f.tag is None and f.candidatos or f.ambiguedad_valor for f in filas):
                    ambiguas += 1
                    continue
                mostradas = [{"tag": f.tag, "valor": f.valor, "unidad": f.unidad} for f in filas if f.valor is not None]
                esperados = [i for i in nota["items"] if i.get("valor") is not None]
                problemas = _comparar(mostradas, esperados)
                if problemas:
                    fallos.append((carpeta, nota["nota_id"], [p["tipo"] for p in problemas], texto[:120]))
                else:
                    perfectas += 1

    print(f"notas {total} | borrador perfecto {perfectas} | con ambigüedad (pregunta) {ambiguas} | "
          f"van al LLM {a_llm} | fallan {len(fallos)}")
    if args.detalle:
        for f in fallos:
            print(" ", *f)


if __name__ == "__main__":
    main()
