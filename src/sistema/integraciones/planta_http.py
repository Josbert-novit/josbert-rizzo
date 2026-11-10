import json
import logging
import time
from pathlib import Path

import httpx

from sistema.config import config

log = logging.getLogger(__name__)

REINTENTOS = 2
ESPERA_ENTRE_REINTENTOS_S = 0.5


class PlantaHTTPError(Exception):
    """Falló la llamada a la API de la planta después de agotar los reintentos."""


class PlantaHTTP:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or config.historian_url

    def _get(self, path: str, params: dict | None = None, timeout: float = 10) -> httpx.Response:
        """GET con reintentos ante errores transitorios (timeout, conexión caída, 5xx). Un 4xx
        (p. ej. 404 de un tag o documento que no existe) no se reintenta: no lo va a arreglar
        reintentar la misma pregunta mal hecha."""
        ultimo_error: Exception | None = None
        for intento in range(REINTENTOS + 1):
            try:
                r = httpx.get(f"{self.base_url}{path}", params=params, timeout=timeout)
                if r.status_code >= 500:
                    r.raise_for_status()
                return r
            except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
                ultimo_error = e
                if intento < REINTENTOS:
                    log.warning("GET %s falló (intento %d/%d): %s", path, intento + 1, REINTENTOS + 1, e)
                    time.sleep(ESPERA_ENTRE_REINTENTOS_S * (intento + 1))
        raise PlantaHTTPError(f"GET {path} falló después de {REINTENTOS + 1} intentos: {ultimo_error}") from ultimo_error

    def equipos(self) -> dict:
        r = self._get("/api/planta")
        r.raise_for_status()
        return r.json()

    def estado(self) -> dict:
        r = self._get("/api/estado")
        r.raise_for_status()
        return r.json()

    def historial(self, tag: str, variable: str, desde: str, hasta: str) -> list[dict]:
        r = self._get(f"/api/historian/{tag}/{variable}", params={"desde": desde, "hasta": hasta})
        r.raise_for_status()
        return r.json()

    def historial_ordenes(
        self, equipo: str | None = None, desde: str | None = None, hasta: str | None = None
    ) -> list[dict]:
        params = {k: v for k, v in {"equipo": equipo, "desde": desde, "hasta": hasta}.items() if v is not None}
        r = self._get("/api/historial-ordenes", params=params)
        r.raise_for_status()
        return r.json()

    def documentos(self) -> dict:
        r = self._get("/api/documentos")
        r.raise_for_status()
        return r.json()

    def secciones(self, doc_id: str, numero: str | None = None) -> list[dict]:
        params = {"numero": numero} if numero else None
        r = self._get(f"/api/documentos/{doc_id}/secciones", params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def fragmentos(self) -> list[dict]:
        """Todas las secciones de todos los documentos más el historial, en un solo archivo
        (`GET /api/documentos` da su ruta en el campo `fragmentos`). Más eficiente para indexar
        todo el corpus (5.1) que pedir `secciones()` documento por documento."""
        r = self._get("/documentos/fragmentos.jsonl", timeout=20)
        r.raise_for_status()
        return [json.loads(linea) for linea in r.text.splitlines() if linea.strip()]

    def archivo_json(self, ruta_relativa: str) -> object:
        """Un archivo estático de `/documentos/...` (p. ej. `evaluacion/retrieval.json`), para
        evaluación (5.3) — no forma parte del pipeline de producción."""
        r = self._get(f"/documentos/{ruta_relativa}", timeout=20)
        r.raise_for_status()
        return r.json()

    def descargar(self, url_relativa: str, destino: Path) -> Path:
        r = self._get(url_relativa, timeout=30)
        r.raise_for_status()
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(r.content)
        return destino
