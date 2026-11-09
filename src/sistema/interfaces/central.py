from fastapi import FastAPI

app = FastAPI(title="Central de mantenimiento")


@app.get("/api/estado")
def estado() -> dict:
    return {"estado": "ok"}
