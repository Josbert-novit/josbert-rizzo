"""Benchmark offline de Whisper (Lote 6, A1): velocidad vs. calidad del parte con audios reales.

Antes de cambiar la configuración de Whisper hay que medir que no se pierdan notas bien
entendidas. Tres pasos:

1. ``generar`` (en el host, con la planta de desarrollo arriba): toma la verdad de campo de las
   corridas guardadas del corrector (texto de cada nota, ítems esperados, si trae golpeteo) y pide
   al contenedor del simulador que arme cada audio con ``planta_sim.audio.nota_a_wav`` — la misma
   función, voz, semilla y ruido de planta que usa en un escenario. Los WAV quedan en
   ``datos_local/whisper_bench/`` (no se versionan).

2. ``transcribir`` (adentro de la imagen del agente, con el mismo límite de CPU que en la planta):

       docker run --rm --cpus 2.5 -v "<repo>/datos_local/whisper_bench:/bench" \\
           -v "<repo>/evaluacion:/app/src/evaluacion" alumno-josbert-rizzo-agente \\
           python -m evaluacion.evaluar_whisper transcribir /bench

   Transcribe todos los audios con cada configuración (``CONFIGS``) y guarda texto y tiempo.

3. ``puntuar`` (en el host, segundos): pasa cada transcripción por el parser determinístico y la
   compara con la verdad igual que ``evaluar_parser`` (función ``_comparar`` del operador
   simulado), y chequea la alerta de seguridad. Imprime una tabla por configuración.
"""

import argparse
import glob
import json
import re
import statistics
import subprocess
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ACADEMIA = RAIZ / "academia2026-tp"
REPO = Path(__file__).resolve().parents[1]
CARPETA = REPO / "datos_local" / "whisper_bench"
CONTENEDOR_SIM = "planta-sim-1"

# Configuraciones a comparar. "actual" = lo que usa el agente antes del Lote 6 (defaults de
# faster-whisper: beam 5, sin VAD, hilos automáticos).
CONFIGS: dict[str, dict] = {
    "actual": {"cpu_threads": 0, "beam_size": 5, "vad_filter": False},
    "h2-b5": {"cpu_threads": 2, "beam_size": 5, "vad_filter": False},
    "h2-b5-vad": {"cpu_threads": 2, "beam_size": 5, "vad_filter": True},
    "h2-b2-vad": {"cpu_threads": 2, "beam_size": 2, "vad_filter": True},
    "h2-b1-vad": {"cpu_threads": 2, "beam_size": 1, "vad_filter": True},
    # Lote 8: el VAD pierde un "golpeteo" (14/19 contra 15/19); sin VAD, ¿cuánto da beam chico?
    "h2-b2": {"cpu_threads": 2, "beam_size": 2, "vad_filter": False},
    "h2-b3": {"cpu_threads": 2, "beam_size": 3, "vad_filter": False},
}

_SCRIPT_SIM = r"""
import json, sys
from pathlib import Path
from planta_sim import audio
manifiesto = json.load(sys.stdin)
destino = Path("/tmp/whisper_bench")
destino.mkdir(parents=True, exist_ok=True)
ok = 0
for n in manifiesto:
    if audio.nota_a_wav(n["texto"], destino / f"{n['clave']}.wav", n["voz"], n["semilla"], n["golpeteo"]):
        ok += 1
print(f"{ok}/{len(manifiesto)} audios generados")
"""


def _notas_de_corridas(corridas: list[str]) -> list[dict]:
    base = ACADEMIA / "correccion" / "corridas"
    carpetas = corridas or sorted(p.name for p in base.iterdir() if p.is_dir())
    vistas: set[str] = set()
    notas = []
    for carpeta in carpetas:
        for archivo in sorted(glob.glob(str(base / carpeta / "*.json"))):
            if archivo.endswith(("resultado.json", "meta.json")):
                continue
            datos = json.loads(Path(archivo).read_text(encoding="utf-8"))
            for nota in datos.get("verdad", []):
                if not isinstance(nota, dict) or "texto" not in nota or nota.get("tipo") == "vivo":
                    continue
                texto = nota["texto"].strip()
                if not texto or texto in vistas:
                    continue
                vistas.add(texto)
                items = nota.get("items") or []
                clave = re.sub(r"[^A-Za-z0-9-]", "_", f"{Path(archivo).stem}-{nota['nota_id']}")[:60] + f"-{len(notas):03d}"
                notas.append({
                    "clave": clave,
                    "nota_id": nota["nota_id"],
                    "texto": texto,
                    "items": items,
                    "fotos": nota.get("fotos") or [],
                    "seguridad": nota.get("seguridad"),
                    "es_parte": "items" in nota and nota["nota_id"].startswith("NOTA-"),
                    "golpeteo": any(i.get("variable") == "ruido" for i in items),
                    "semilla": zlib.crc32(nota["nota_id"].encode()),
                    "voz": "es_AR-daniela-high",
                })
    return notas


def generar(args: argparse.Namespace) -> None:
    notas = _notas_de_corridas(args.corridas)
    if args.max:
        notas = notas[: args.max]
    CARPETA.mkdir(parents=True, exist_ok=True)
    (CARPETA / "manifiesto.json").write_text(json.dumps(notas, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(notas)} notas distintas; generando audios en {CONTENEDOR_SIM}...")
    subprocess.run(["docker", "exec", CONTENEDOR_SIM, "rm", "-rf", "/tmp/whisper_bench"], check=True)
    subprocess.run(
        ["docker", "exec", "-i", "-w", "/app", CONTENEDOR_SIM, "python", "-c", _SCRIPT_SIM],
        input=json.dumps(notas).encode(), check=True,
    )
    subprocess.run(["docker", "cp", f"{CONTENEDOR_SIM}:/tmp/whisper_bench/.", str(CARPETA)], check=True)
    print(f"listo: {CARPETA}")


def transcribir(args: argparse.Namespace) -> None:
    from faster_whisper import WhisperModel

    carpeta = Path(args.carpeta)
    manifiesto = json.loads((carpeta / "manifiesto.json").read_text(encoding="utf-8"))
    nombres = args.configs or list(CONFIGS)
    for nombre in nombres:
        cfg = CONFIGS[nombre]
        modelo = WhisperModel(args.modelo, device="cpu", compute_type="int8", cpu_threads=cfg["cpu_threads"])
        # Calentamiento: la primera transcripción paga la inicialización, no cuenta.
        primero = carpeta / f"{manifiesto[0]['clave']}.wav"
        list(modelo.transcribe(str(primero), language="es", beam_size=cfg["beam_size"], vad_filter=cfg["vad_filter"])[0])
        resultados = {}
        for n in manifiesto:
            wav = carpeta / f"{n['clave']}.wav"
            if not wav.exists():
                continue
            t0 = time.perf_counter()
            segmentos, info = modelo.transcribe(
                str(wav), language="es", beam_size=cfg["beam_size"], vad_filter=cfg["vad_filter"]
            )
            texto = " ".join(s.text.strip() for s in segmentos).strip()
            resultados[n["clave"]] = {"texto": texto, "s": time.perf_counter() - t0, "audio_s": info.duration}
        (carpeta / f"transcripciones_{nombre}.json").write_text(
            json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tiempos = [r["s"] for r in resultados.values()]
        print(f"{nombre}: {len(tiempos)} audios, mediana {statistics.median(tiempos):.1f} s", flush=True)
        del modelo


def _p90(valores: list[float]) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(round(0.9 * (len(ordenados) - 1))))]


def puntuar(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(ACADEMIA / "planta" / "sim"))
    import yaml
    from planta_sim.dialogo import _comparar

    from sistema.procesamiento.novedades import novedades_del_texto
    from sistema.procesamiento.parser_notas import ParserPartes, catalogo_desde_planta
    from sistema.procesamiento.resolucion import ResolverNombres
    from sistema.procesamiento.seguridad import detectar

    planta = yaml.safe_load((ACADEMIA / "datos" / "planta.yaml").read_text(encoding="utf-8"))
    parser = ParserPartes(catalogo_desde_planta(planta))

    class _HttpFijo:
        def equipos(self):
            return planta

    resolver = ResolverNombres(_HttpFijo())
    manifiesto = {n["clave"]: n for n in json.loads((CARPETA / "manifiesto.json").read_text(encoding="utf-8"))}

    print(f"{'config':<12} {'notas':>5} {'med s':>6} {'p90 s':>6} {'x audio':>7} {'perfecta':>8} "
          f"{'pregunta':>8} {'LLM':>4} {'falla':>5} {'segur.':>7} {'golpet.':>7}")
    for archivo in sorted(CARPETA.glob("transcripciones_*.json")):
        nombre = archivo.stem.removeprefix("transcripciones_")
        res = json.loads(archivo.read_text(encoding="utf-8"))
        perfectas = ambiguas = a_llm = fallan = seg_ok = seg_total = golpe_ok = golpe_total = 0
        detalle = []
        for clave, r in res.items():
            n = manifiesto.get(clave)
            if n is None:
                continue
            texto = r["texto"]
            if n["seguridad"]:
                seg_total += 1
                seg_ok += detectar(texto) is not None
                continue
            if not n.get("es_parte"):
                continue
            if detectar(texto) is not None:
                detalle.append((clave, "falsa alarma de seguridad", texto[:100]))
            if n["golpeteo"]:
                golpe_total += 1
                try:
                    dichas = novedades_del_texto(texto, resolver.equipos_mencionados)
                except Exception:
                    dichas = {}
                golpe_ok += any("golpe" in " ".join(v).lower() or "ruido" in " ".join(v).lower() for v in dichas.values())
            filas = parser.parsear(texto, datetime.now(timezone.utc), n.get("fotos") or [])
            if filas is None:
                a_llm += 1
                detalle.append((clave, "LLM", texto[:100]))
                continue
            if any(f.tag is None and f.candidatos or f.ambiguedad_valor for f in filas):
                ambiguas += 1
                continue
            mostradas = [{"tag": f.tag, "valor": f.valor, "unidad": f.unidad} for f in filas if f.valor is not None]
            esperados = [i for i in n["items"] if i.get("valor") is not None]
            if _comparar(mostradas, esperados):
                fallan += 1
                detalle.append((clave, "falla", texto[:100]))
            else:
                perfectas += 1
        tiempos = [r["s"] for r in res.values()]
        ratio = sum(r["s"] for r in res.values()) / max(1e-9, sum(r["audio_s"] for r in res.values()))
        print(f"{nombre:<12} {len(res):>5} {statistics.median(tiempos):>6.1f} {_p90(tiempos):>6.1f} {ratio:>7.2f} "
              f"{perfectas:>8} {ambiguas:>8} {a_llm:>4} {fallan:>5} {seg_ok:>3}/{seg_total:<3} {golpe_ok:>3}/{golpe_total:<3}")
        if args.detalle:
            for d in detalle:
                print("   ", *d)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="paso", required=True)
    g = sub.add_parser("generar")
    g.add_argument("corridas", nargs="*")
    g.add_argument("--max", type=int, default=0)
    t = sub.add_parser("transcribir")
    t.add_argument("carpeta")
    t.add_argument("--configs", nargs="*")
    t.add_argument("--modelo", default="small")
    p = sub.add_parser("puntuar")
    p.add_argument("-v", "--detalle", action="store_true")
    args = ap.parse_args()
    {"generar": generar, "transcribir": transcribir, "puntuar": puntuar}[args.paso](args)


if __name__ == "__main__":
    main()
