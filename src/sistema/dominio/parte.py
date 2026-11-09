from enum import Enum

from planta_contrato import FilaParte
from pydantic import BaseModel, Field


class EstadoParte(str, Enum):
    borrador = "Borrador"
    confirmado = "Confirmado"


class Parte(BaseModel):
    id: str
    nota_ids: list[str] = Field(default_factory=list)
    estado: EstadoParte = EstadoParte.borrador
    filas: list[FilaParte] = Field(default_factory=list)
