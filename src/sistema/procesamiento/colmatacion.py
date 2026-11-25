"""Colmatación acelerada de un filtro por la tendencia del historian (Lote 9, BUGS #78).

El anexo de la Entrega 2 pide usar la "tendencia del historian" como evidencia. Un filtro que se
tapa más rápido que de costumbre no llega a la alarma alta (1,5 kg/cm²) hasta muchas horas después,
pero su presión diferencial ya sube varias veces más rápido que en los ciclos anteriores. El manual
del filtro lo nombra (FC48-3 §4.2, "Colmatación acelerada"): "Si la presión diferencial sube varias
veces más rápido que en los cambios anteriores, la causa no está en el filtro: cambió la calidad del
agua". Es el caso de `sin-luz` (F07 en F-201B): sin esto no había ninguna orden antes del corte.

Cómo se mide (todo en código, sin LLM):
1. La ΔP depende del cuadrado del caudal (FC48-3 §7.1): se corrige al caudal de diseño con FT-401,
   `ΔP_corr = ΔP · (Q_diseño / Q)²`. Sin esto la ondulación diaria del caudal se confunde con
   ensuciamiento.
2. Una caída brusca de la ΔP corregida es un cambio de cartuchos: separa ciclos.
3. Pendiente base: mediana de las pendientes (regresión lineal) de los ciclos anteriores y del
   tramo viejo del ciclo actual. Pendiente reciente: regresión sobre las últimas horas.
4. Escala si la reciente es `factor` veces la base y supera un mínimo absoluto, en dos revisiones
   seguidas. El evento sigue el camino de cualquier escalado de sensor de diferencial: orden
   inmediata T-F-CAR y las reglas de aprobación de siempre.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from planta_contrato import EventoSistema, TipoEvento

log = logging.getLogger(__name__)

VARIABLE_DP = "presion_diferencial"
CITA_MANUAL = "[FC48-3 §4.2]"


@dataclass(frozen=True)
class Umbrales:
    q_diseno: float = 2400.0          # m3/d (FC48-3 §7.1 y `inyeccion_programada_m3d`)
    ventana_h: float = 4.0            # pendiente reciente
    factor: float = 3.0               # "varias veces más rápido"
    pendiente_min: float = 0.004      # kg/cm²/h: por debajo, no importa cuántas veces sea
    caida_cambio: float = 0.4         # caída de la ΔP corregida que marca un cambio de cartuchos
    minutos_cambio: int = 30          # ...si se sostiene este tiempo
    min_horas_base: float = 12.0      # largo mínimo de un tramo para estimar la pendiente base
    min_puntos_ventana: int = 30
    q_min_relativo: float = 0.5       # con menos caudal (bombas paradas, sin luz) no se corrige
    hueco_max_min: float = 5.0        # un hueco más largo en los datos válidos es un corte...
    salto_caudal: float = 0.03        # ...igual que un salto de caudal de más de 3 % en un minuto
    estabilizacion_min: float = 30.0  # y se descartan los minutos siguientes (transitorio)


UMBRALES = Umbrales()


@dataclass(frozen=True)
class Hallazgo:
    tag: str
    pendiente_reciente: float         # kg/cm²/h, corregida por caudal
    pendiente_base: float
    veces: float
    dp_actual: float                  # última lectura sin corregir
    inicio_ciclo: datetime


def _ts(valor) -> datetime:
    return valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor))


def _serie(puntos: list[dict]) -> list[tuple[datetime, float]]:
    return [(_ts(p["ts"]), float(p["valor"])) for p in puntos if p.get("valor") is not None]


def corregir_por_caudal(
    dp: list[tuple[datetime, float]], caudal: list[tuple[datetime, float]], u: Umbrales,
) -> list[tuple[datetime, float]]:
    """Cada ΔP con el último caudal conocido hasta ese momento. Se descartan los puntos sin caudal
    o con caudal muy bajo (no son comparables), y los `estabilizacion_min` siguientes a un hueco de
    datos válidos (corte de luz, bombas paradas) o a un salto brusco del caudal (arranque o disparo
    de una bomba): la ΔP vuelve desde casi 0 en unos minutos, y esos puntos al principio de la
    ventana parecían una subida rápida (falsos positivos en F-201A después del corte de `sin-luz`)."""
    salida: list[tuple[datetime, float]] = []
    j, q_actual = 0, None
    ultimo_valido: tuple[datetime, float] | None = None
    estable_desde: datetime | None = None
    for t, v in dp:
        while j < len(caudal) and caudal[j][0] <= t:
            q_actual = caudal[j][1]
            j += 1
        if q_actual is None or q_actual < u.q_min_relativo * u.q_diseno:
            continue
        if ultimo_valido is not None:
            t_prev, q_prev = ultimo_valido
            hueco = t - t_prev > timedelta(minutes=u.hueco_max_min)
            salto = abs(q_actual - q_prev) > u.salto_caudal * q_prev
            if hueco or salto:
                estable_desde = t + timedelta(minutes=u.estabilizacion_min)
        ultimo_valido = (t, q_actual)
        if estable_desde is not None and t < estable_desde:
            continue
        salida.append((t, v * (u.q_diseno / q_actual) ** 2))
    return salida


def _pendiente_por_hora(puntos: list[tuple[datetime, float]]) -> float | None:
    if len(puntos) < 3:
        return None
    t0 = puntos[0][0]
    xs = [(t - t0).total_seconds() / 3600 for t, _ in puntos]
    ys = [v for _, v in puntos]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def ciclos(serie: list[tuple[datetime, float]], u: Umbrales) -> list[list[tuple[datetime, float]]]:
    """Parte la serie en cada cambio de cartuchos: una caída brusca de la ΔP corregida que se
    SOSTIENE `minutos_cambio` (un transitorio al volver la luz o al arrancar una bomba no lo es)."""
    tramos: list[list[tuple[datetime, float]]] = []
    actual: list[tuple[datetime, float]] = []
    for i, punto in enumerate(serie):
        if actual and actual[-1][1] - punto[1] > u.caida_cambio:
            hasta = punto[0] + timedelta(minutes=u.minutos_cambio)
            despues = [v for t, v in serie[i:i + 4 * u.minutos_cambio] if t <= hasta]
            if statistics.median(despues) < actual[-1][1] - u.caida_cambio:
                tramos.append(actual)
                actual = []
        actual.append(punto)
    if actual:
        tramos.append(actual)
    return tramos


def _horas(tramo: list[tuple[datetime, float]]) -> float:
    return (tramo[-1][0] - tramo[0][0]).total_seconds() / 3600 if len(tramo) > 1 else 0.0


def evaluar_colmatacion(
    tag: str, dp_crudo: list[dict], caudal_crudo: list[dict], ahora: datetime, u: Umbrales = UMBRALES,
    alarma_alta: float | None = None,
) -> Hallazgo | None:
    """`Hallazgo` si la ΔP sube `factor` veces más rápido que en los ciclos anteriores; `None` si
    no, o si no hay historial suficiente para decirlo.

    Con la ΔP ya en `alarma_alta` no opina: de ahí en adelante escala el detector de alarma
    sostenida (`eventos.py::DetectorSensores`) con la misma T-F-CAR. La tendencia es para
    anticiparse a la alarma. Medido en `evaluar_colmatacion` (sin-luz, semilla 3404): con los dos
    filtros sucios la succión cae, las bombas cavitan y el caudal oscila entre pasos; ahí la
    corrección por caudal deja de ser fiel y el F-201A, ya en alarma desde hacía horas, parecía
    acelerarse."""
    dp = _serie(dp_crudo)
    if alarma_alta is not None and dp:
        recientes = [v for t, v in dp if t >= ahora - timedelta(hours=1)]
        if recientes and statistics.median(recientes) >= alarma_alta:
            return None
    corregida = corregir_por_caudal(dp, _serie(caudal_crudo), u)
    if not corregida:
        return None
    tramos = ciclos(corregida, u)
    actual = tramos[-1]
    desde = ahora - timedelta(hours=u.ventana_h)
    ventana = [p for p in actual if p[0] >= desde]
    # La ventana tiene que estar entera dentro del ciclo actual (un cambio de cartuchos en el medio
    # no es ensuciamiento) y tener puntos suficientes.
    if len(ventana) < u.min_puntos_ventana or actual[0][0] > desde:
        return None
    reciente = _pendiente_por_hora(ventana)
    if reciente is None:
        return None

    candidatos = [t for t in tramos[:-1] if _horas(t) >= u.min_horas_base]
    previo_en_ciclo = [p for p in actual if p[0] < desde - timedelta(hours=u.ventana_h)]
    if _horas(previo_en_ciclo) >= u.min_horas_base:
        candidatos.append(previo_en_ciclo)
    pendientes = [p for p in (_pendiente_por_hora(t) for t in candidatos) if p is not None]
    if not pendientes:
        return None
    # Piso para la base: un ciclo casi plano (o con ruido negativo) no puede volver "infinitas
    # veces más rápido" cualquier cosa; el mínimo absoluto hace el resto.
    base = max(statistics.median(pendientes), u.pendiente_min / (2 * u.factor))

    if reciente < u.pendiente_min or reciente < u.factor * base:
        return None
    return Hallazgo(
        tag=tag, pendiente_reciente=reciente, pendiente_base=base, veces=reciente / base,
        dp_actual=dp[-1][1], inicio_ciclo=actual[0][0],
    )


def id_evento(tag: str, ahora: datetime) -> str:
    """Determinístico por día: si el agente se reinicia (corte de luz) vuelve a salir el mismo id,
    y la orden de ese evento ya está en Postgres (no se decide de nuevo ni se duplica)."""
    return f"EVT-SENSOR-{tag}-{VARIABLE_DP}-colmatacion-{ahora.date().isoformat()}"


def evento_de(h: Hallazgo, ahora: datetime) -> EventoSistema:
    resumen = (
        f"{h.tag}/{VARIABLE_DP}: colmatación acelerada, la presión diferencial sube "
        f"{h.pendiente_reciente:.3f} kg/cm2/h ({h.veces:.1f} veces más rápido que en los ciclos "
        f"anteriores, {h.pendiente_base:.4f} kg/cm2/h), ahora {h.dp_actual:.2f} kg/cm2 {CITA_MANUAL}. "
        "Programar el cambio de cartuchos y revisar la calidad del agua con tratamiento."
    )
    return EventoSistema(
        id=id_evento(h.tag, ahora), ts=ahora, tipo=TipoEvento.escalado, equipo=h.tag,
        origen="sensor", resumen=resumen,
    )


def detector_desde_planta(http, umbrales: Umbrales | None = None, **kwargs) -> DetectorColmatacion:
    """Filtros, caudalímetro y caudal de diseño salen de `GET /api/planta`, no hardcodeados."""
    datos = http.equipos()
    filtros = [e["tag"] for e in datos.get("equipos", []) if e.get("tipo") == "filtro"]
    caudal_tag = next((s["tag"] for s in datos.get("sensores", []) if s.get("variable") == "caudal"), "FT-401")
    q_diseno = (datos.get("planta") or {}).get("inyeccion_programada_m3d")
    alarmas = {
        s["tag"]: float(s["alarma_alta"]) for s in datos.get("sensores", [])
        if s.get("variable") == VARIABLE_DP and s.get("alarma_alta") is not None
    }
    u = umbrales or Umbrales()
    if q_diseno:
        u = Umbrales(**{**u.__dict__, "q_diseno": float(q_diseno)})
    return DetectorColmatacion(http, filtros, caudal_tag, u, alarmas=alarmas, **kwargs)


class DetectorColmatacion:
    """Revisa cada filtro cada `intervalo` de planta y escala con `confirmaciones` revisiones
    positivas seguidas. Un filtro ya escalado no vuelve a escalar en el mismo ciclo de cartuchos.

    El historial largo (para la pendiente base) se pide una vez por filtro y se completa después
    solo con lo nuevo: el historian de 21-45 días son decenas de miles de puntos."""

    def __init__(
        self, http, filtros: list[str], caudal_tag: str = "FT-401", umbrales: Umbrales = UMBRALES,
        *, intervalo: timedelta = timedelta(minutes=30), confirmaciones: int = 2,
        historia: timedelta = timedelta(days=45), alarmas: dict[str, float] | None = None,
    ) -> None:
        self._http = http
        self._alarmas = alarmas or {}
        self._filtros = list(filtros)
        self._caudal_tag = caudal_tag
        self._u = umbrales
        self._intervalo = intervalo
        self._confirmaciones = confirmaciones
        self._historia = historia
        self._ultima_revision: datetime | None = None
        self._positivas: dict[str, int] = {}
        self._escalado_en_ciclo: dict[str, datetime] = {}
        self._cache: dict[tuple[str, str], list[dict]] = {}

    def _historial(self, tag: str, variable: str, ahora: datetime) -> list[dict]:
        clave = (tag, variable)
        previos = self._cache.get(clave)
        desde = _ts(previos[-1]["ts"]) if previos else ahora - self._historia
        nuevos = self._http.historial(tag, variable, desde.isoformat(), ahora.isoformat())
        if previos:
            nuevos = [p for p in nuevos if _ts(p["ts"]) > desde]
        datos = (previos or []) + nuevos
        limite = ahora - self._historia
        datos = [p for p in datos if _ts(p["ts"]) >= limite]
        self._cache[clave] = datos
        return datos

    def _reiniciar(self) -> None:
        self._ultima_revision = None
        self._positivas.clear()
        self._escalado_en_ciclo.clear()
        self._cache.clear()

    def revisar(self, ahora: datetime) -> list[EventoSistema]:
        # El reloj de planta volvió atrás o saltó días: otro escenario (el simulador borra su
        # historian al iniciar). Lo guardado ya no corresponde.
        if self._ultima_revision is not None and (
            ahora < self._ultima_revision or ahora - self._ultima_revision > timedelta(days=2)
        ):
            self._reiniciar()
        if self._ultima_revision is not None and ahora - self._ultima_revision < self._intervalo:
            return []
        self._ultima_revision = ahora
        caudal = self._historial(self._caudal_tag, "caudal", ahora)
        eventos: list[EventoSistema] = []
        for tag in self._filtros:
            try:
                hallazgo = evaluar_colmatacion(
                    tag, self._historial(tag, VARIABLE_DP, ahora), caudal, ahora, self._u,
                    alarma_alta=self._alarmas.get(tag),
                )
            except Exception:
                log.exception("colmatación: no se pudo evaluar %s", tag)
                continue
            if hallazgo is None:
                self._positivas[tag] = 0
                continue
            if self._escalado_en_ciclo.get(tag) == hallazgo.inicio_ciclo:
                continue
            self._positivas[tag] = self._positivas.get(tag, 0) + 1
            log.info(
                "colmatación %s: %.4f kg/cm2/h, %.1f veces la base (%d/%d)", tag,
                hallazgo.pendiente_reciente, hallazgo.veces, self._positivas[tag], self._confirmaciones,
            )
            if self._positivas[tag] >= self._confirmaciones:
                self._escalado_en_ciclo[tag] = hallazgo.inicio_ciclo
                self._positivas[tag] = 0
                eventos.append(evento_de(hallazgo, ahora))
        return eventos
