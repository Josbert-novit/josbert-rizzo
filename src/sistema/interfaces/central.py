import json

from fastapi import FastAPI, HTTPException

from sistema.config import config

app = FastAPI(title="Central de mantenimiento")


@app.get("/api/estado")
def estado() -> dict:
    return {"estado": "ok"}


@app.get("/api/partes")
def partes() -> list[dict]:
    """Los partes que arma el agente (uno por nota), para inspección y para la evaluación de la
    Fase 4.3 (comparar contra `GET /api/verdad` de la planta)."""
    if not config.media_dir.exists():
        return []
    resultado = []
    for archivo in sorted(config.media_dir.glob("*/parte.json")):
        resultado.append(json.loads(archivo.read_text(encoding="utf-8")))
    return resultado


@app.get("/api/partes/{nota_id}")
def parte(nota_id: str) -> dict:
    archivo = config.media_dir / nota_id / "parte.json"
    if not archivo.exists():
        raise HTTPException(404, f"No hay parte para la nota {nota_id}")
    return json.loads(archivo.read_text(encoding="utf-8"))
