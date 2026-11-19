from datetime import datetime
from enum import Enum

from planta_contrato import FilaParte
from pydantic import BaseModel, ConfigDict, Field


class EstadoParte(str, Enum):
    borrador = "Borrador"
    confirmado = "Confirmado"


class FilaParteInterna(BaseModel):
    """Una fila del parte con los datos internos que el contrato (FilaParte) no lleva:
    la hora de la nota (no la del reloj de la máquina), las fotos asociadas, el nombre tal
    como lo dijo el operador y el resultado de resolverlo contra el listado de equipos."""

    model_config = ConfigDict(extra="forbid")

    tag_dicho: str
    tag: str | None = None
    ambiguo: bool = False
    candidatos: list[str] = Field(default_factory=list)
    variable: str
    valor: float | None = None
    unidad: str | None = None
    observacion: str | None = None
    hora: datetime
    fotos: list[str] = Field(default_factory=list)
    requiere_confirmacion: bool = False
    motivo_confirmacion: str | None = None
    # Lote 5: el valor se dijo de una forma ambigua ("dieciocho" de diferencial): la pregunta lleva
    # opciones para que cuente como pregunta (A4a), además del borrador.
    ambiguedad_valor: bool = False

    def a_contrato(self) -> FilaParte:
        return FilaParte(
            tag=self.tag or self.tag_dicho,
            variable=self.variable,
            valor=self.valor,
            unidad=self.unidad,
            observacion=self.observacion,
        )


class Parte(BaseModel):
    id: str
    nota_ids: list[str] = Field(default_factory=list)
    estado: EstadoParte = EstadoParte.borrador
    filas: list[FilaParteInterna] = Field(default_factory=list)
    # Transcripción de la nota (Lote 4, H15): para detectar fallas dichas de palabra al confirmar.
    texto: str = ""
