from pathlib import Path

import httpx

from sistema.config import config


class PlantaHTTP:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or config.historian_url

    def equipos(self) -> dict:
        r = httpx.get(f"{self.base_url}/api/planta", timeout=10)
        r.raise_for_status()
        return r.json()

    def historial(self, tag: str, variable: str, desde: str, hasta: str) -> list[dict]:
        r = httpx.get(
            f"{self.base_url}/api/historian/{tag}/{variable}",
            params={"desde": desde, "hasta": hasta},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def descargar(self, url_relativa: str, destino: Path) -> Path:
        r = httpx.get(f"{self.base_url}{url_relativa}", timeout=30)
        r.raise_for_status()
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(r.content)
        return destino
