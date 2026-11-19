"""Diálogo con el operador por la tablet: manda el borrador del parte, entiende la respuesta
en lenguaje de campo (confirmación, corrección o elección entre equipos ambiguos) y repite
hasta que el operador confirma todo. Recién ahí el parte pasa de Borrador a Confirmado.

Simplificación de esta etapa: el estado del diálogo vive en memoria (se pierde si el proceso se
reinicia). Persistirlo en Postgres es parte de 5.7 (memoria de conversación).
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from planta_contrato import MensajeTablet, Pantalla

from sistema.dominio.parte import EstadoParte, Parte
from sistema.llm.control import ConsumoTokens, PresupuestoAgotado, verificar_presupuesto
from sistema.llm.extraccion import Extractor
from sistema.procesamiento.interpretacion import interpretar_texto, parser_configurado
from sistema.procesamiento.rango_historico import VerificadorRango
from sistema.procesamiento.resolucion import ResolverNombres
from sistema.procesamiento.resolucion import _normalizar as _normalizar_dicho
from sistema.procesamiento.respuestas_operador import aplicar_correcciones, tiene_valores
from sistema.procesamiento.variables import mapear_variable
from sistema.reloj import reloj

PALABRAS_CONFIRMACION = ("si", "sí", "dale", "confirmo", "correcto", "listo", "ok", "esta bien", "está bien")

MAX_INTENTOS_DESAMBIGUACION = 3

# Detección de loops (Anexo 3.5): freno GENÉRICO que se suma a los topes puntuales de cada rama
# (presupuesto de pasos, intentos de desambiguación). Si la misma pregunta exacta (texto + opciones
# + filas) se va a mandar por cuarta vez (los topes propios permiten hasta 3), o el diálogo pasa de MAX_TURNOS_DIALOGO mensajes, el
# diálogo no está avanzando: se acepta el parte como está, marcado para revisión manual, en vez de
# seguir preguntando. Cubre cualquier rama nueva que se olvide de su propio freno (BUGS.md #13/#17
# fueron exactamente eso).
MAX_REPETICIONES_PREGUNTA = 3
MAX_TURNOS_DIALOGO = 12
NOTA_LOOP = "diálogo cortado por detección de loop (la misma pregunta se repetía): revisar a mano"


def _es_confirmacion(texto: str) -> bool:
    normalizado = texto.strip().lower()
    if normalizado.startswith("no"):
        return False
    return any(normalizado.startswith(palabra) for palabra in PALABRAS_CONFIRMACION)


def _siguiente_fila_pendiente(parte: Parte) -> int | None:
    for i, fila in enumerate(parte.filas):
        # Lote 3 (H13): sin equipo, sin candidatos y sin valor no hay nada que preguntar — antes
        # se preguntaba "¿Podés repetirlo?" y el operador nunca lo resolvía (callejón sin salida).
        if fila.tag is None and not fila.candidatos and fila.valor is None:
            continue
        if fila.tag is None or fila.requiere_confirmacion:
            return i
    return None


@dataclass
class EstadoDialogo:
    parte: Parte
    mensaje_id_pendiente: str | None = None
    fila_en_pregunta: int | None = None
    consumo: ConsumoTokens = field(default_factory=ConsumoTokens)
    intentos_desambiguacion: dict[int, int] = field(default_factory=dict)
    firmas_preguntas: list[str] = field(default_factory=list)


def _presupuesto_disponible(estado: "EstadoDialogo") -> bool:
    try:
        verificar_presupuesto(estado.consumo)
        return True
    except PresupuestoAgotado:
        return False


def construir_mensaje(estado: EstadoDialogo) -> MensajeTablet:
    indice = _siguiente_fila_pendiente(estado.parte)
    estado.fila_en_pregunta = indice
    opciones: list[str] = []

    eleccion_de_equipo = False
    if indice is not None:
        fila = estado.parte.filas[indice]
        if fila.tag is None and fila.candidatos:
            texto = f'¿A cuál equipo te referís con "{fila.tag_dicho}"?'
            opciones = list(fila.candidatos)
            eleccion_de_equipo = True
        elif fila.tag is None:
            texto = f'No encontré ningún equipo para "{fila.tag_dicho}". ¿Podés repetirlo?'
        else:
            texto = f"{fila.tag_dicho} ({fila.variable}): {fila.motivo_confirmacion}. ¿Confirmás el valor?"
            if fila.ambiguedad_valor:
                # Lote 5: un valor dicho de forma ambigua ("dieciocho" de diferencial) es una
                # pregunta de verdad: lleva opciones (así cuenta como pregunta) y también el
                # borrador, para que el operador vea el valor propuesto y lo confirme o corrija.
                opciones = ["Sí", "No"]
    else:
        texto = "Parte armado con lo que entendí. ¿Confirmás todo como está?"

    # Si es una pregunta de elegir un equipo, la pantalla no lleva el borrador: mezclar las dos
    # cosas hace ambigua la pregunta en sí (¿elegí un equipo, o estoy corrigiendo el parte?).
    filas_pantalla = [] if eleccion_de_equipo else [f.a_contrato() for f in estado.parte.filas]

    mensaje = MensajeTablet(
        id=str(uuid.uuid4()),
        ts=reloj.ahora(),
        nota_ids=estado.parte.nota_ids,
        texto=texto,
        pantalla=Pantalla(
            titulo="Confirmar parte",
            filas=filas_pantalla,
            opciones=opciones,
        ),
        espera_respuesta=True,
    )
    estado.mensaje_id_pendiente = mensaje.id
    return mensaje


def firma_pregunta(mensaje: MensajeTablet) -> str:
    """Lo que ve el operador, sin id ni hora: dos mensajes con la misma firma son la misma pregunta."""
    pantalla = mensaje.pantalla.model_dump(mode="json") if mensaje.pantalla else None
    crudo = json.dumps({"texto": mensaje.texto, "pantalla": pantalla}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(crudo.encode("utf-8")).hexdigest()


def detectar_loop(estado: EstadoDialogo, mensaje: MensajeTablet) -> bool:
    """Registra la pregunta que se va a mandar. Si es un loop, cierra el diálogo (parte
    Confirmado con nota de revisión manual) y devuelve True: el caller NO debe mandar el mensaje."""
    firma = firma_pregunta(mensaje)
    repetida = estado.firmas_preguntas.count(firma) >= MAX_REPETICIONES_PREGUNTA
    demasiados_turnos = len(estado.firmas_preguntas) >= MAX_TURNOS_DIALOGO
    if not (repetida or demasiados_turnos):
        estado.firmas_preguntas.append(firma)
        return False
    for f in estado.parte.filas:
        if NOTA_LOOP not in (f.observacion or ""):
            f.observacion = f"{f.observacion} / {NOTA_LOOP}" if f.observacion else NOTA_LOOP
        f.requiere_confirmacion = False
        f.motivo_confirmacion = None
        if f.tag is None:
            f.tag = f.candidatos[0] if f.candidatos else f.tag_dicho
            f.ambiguo = False
            f.candidatos = []
    estado.parte.estado = EstadoParte.confirmado
    return True


def _mismo_equipo(existente, nueva) -> bool:
    if nueva.tag and existente.tag:
        return existente.tag == nueva.tag
    # Al menos uno de los dos todavía no se resolvió contra el catálogo: comparar el nombre dicho
    # de forma flexible (substring, no igualdad exacta). Ver BUGS.md #18: la extracción puede
    # frasear el mismo equipo distinto entre llamadas (p. ej. "el de la izquierda" en la nota
    # original vs. "el filtro A" en una corrección posterior) — exigir texto idéntico dejaba la
    # fila vieja huérfana (duplicada) en vez de reemplazarla.
    dicho_existente, dicho_nueva = _normalizar_dicho(existente.tag_dicho), _normalizar_dicho(nueva.tag_dicho)
    return bool(dicho_existente) and bool(dicho_nueva) and (dicho_existente in dicho_nueva or dicho_nueva in dicho_existente)


def _mergear_correccion(parte: Parte, filas_nuevas: list) -> None:
    for nueva in filas_nuevas:
        encontrada = False
        for i, existente in enumerate(parte.filas):
            misma_variable = mapear_variable(existente.variable) == mapear_variable(nueva.variable)
            if misma_variable and _mismo_equipo(existente, nueva):
                parte.filas[i] = nueva
                encontrada = True
                break
        if not encontrada:
            parte.filas.append(nueva)


def _respuesta_sin_llm(estado: EstadoDialogo, texto_respuesta: str) -> bool | None:
    """Lote 5: la respuesta del operador entendida sin LLM (`planta_kit.respuestas`, de la cátedra).
    Devuelve lo mismo que `procesar_respuesta` (True = parte confirmado), o `None` si no se entendió
    o no aplica a la pregunta en curso: ahí sigue el camino de siempre, con el LLM."""
    parser = parser_configurado()
    if parser is None:
        return None
    try:
        from planta_kit.respuestas import interpretar

        respuesta = interpretar(texto_respuesta, parser.resolutor)
    except Exception:
        return None

    indice = estado.fila_en_pregunta
    fila = estado.parte.filas[indice] if indice is not None and indice < len(estado.parte.filas) else None

    if respuesta.tipo == "confirma":
        if fila is None:
            estado.parte.estado = EstadoParte.confirmado
            return True
        if fila.tag is not None:
            fila.requiere_confirmacion = False
            fila.motivo_confirmacion = None
            fila.ambiguedad_valor = False
            return False
        return None
    if respuesta.tipo == "elige":
        if fila is not None and fila.tag is None and respuesta.tag_elegido:
            fila.tag, fila.ambiguo, fila.candidatos = respuesta.tag_elegido, False, []
            return False
        return None
    if respuesta.tipo == "corrige":
        aplicar_correcciones(estado.parte, respuesta, parser.catalogo)
        return False
    if respuesta.tipo == "no_entiende" and not tiene_valores(estado.parte):
        # "¿Confirmar qué? Mostrame lo que anotaste.": la nota no tiene ningún número que mostrar
        # (solo estados u observaciones), así que no hay nada que el operador pueda confirmar.
        # Seguir preguntando solo gasta llamadas al LLM (Lote 5).
        nota_revision = "parte sin valores numéricos: se registra tal cual lo dictó el operador"
        for f in estado.parte.filas:
            f.observacion = f"{f.observacion} / {nota_revision}" if f.observacion else nota_revision
        estado.parte.estado = EstadoParte.confirmado
        return True
    return None


def procesar_respuesta(
    estado: EstadoDialogo,
    texto_respuesta: str,
    extractor: Extractor,
    resolver: ResolverNombres,
    verificador_rango: VerificadorRango,
) -> bool:
    """Interpreta la respuesta del operador y actualiza el estado del diálogo.

    Devuelve True si con esta respuesta el parte quedó Confirmado (ya no hace falta mandar
    otro mensaje a la tablet); False si hay que seguir preguntando."""
    resultado = _respuesta_sin_llm(estado, texto_respuesta)
    if resultado is not None:
        return resultado

    indice = estado.fila_en_pregunta

    if indice is None:
        if _es_confirmacion(texto_respuesta):
            estado.parte.estado = EstadoParte.confirmado
            return True
        if _presupuesto_disponible(estado):
            filas_corregidas = interpretar_texto(
                texto_respuesta, reloj.ahora(), [], extractor, resolver, verificador_rango, estado.consumo
            )
            _mergear_correccion(estado.parte, filas_corregidas)
            return False
        # Se agotó el presupuesto de pasos (ver BUGS.md #17): si el operador nunca dice un "sí"
        # literal, no reinterpretar más dejaría esta pregunta ("¿confirmás todo?") repitiéndose
        # para siempre. Se da el parte por confirmado tal como está, marcado para revisión manual
        # (misma idea que #13, aplicada acá: esta rama se había quedado sin el mismo freno).
        nota_revision = "parte aceptado automáticamente al agotarse el presupuesto de pasos: revisar a mano"
        for f in estado.parte.filas:
            f.observacion = f"{f.observacion} / {nota_revision}" if f.observacion else nota_revision
        estado.parte.estado = EstadoParte.confirmado
        return True

    fila = estado.parte.filas[indice]

    if fila.tag is None and fila.candidatos:
        resolucion = resolver.resolver(texto_respuesta)
        if resolucion.tag:
            fila.tag = resolucion.tag
            fila.ambiguo = False
            fila.candidatos = []
            return False
        # No se resolvió de nuevo (ver BUGS.md #15: una respuesta mal interpretada puede quedar
        # ambigua para siempre). Sin este tope, al no haber ningún llamado al LLM en esta rama,
        # el presupuesto de pasos (4.1) nunca se agota y la pregunta se repetiría sin límite.
        estado.intentos_desambiguacion[indice] = estado.intentos_desambiguacion.get(indice, 0) + 1
        if estado.intentos_desambiguacion[indice] >= MAX_INTENTOS_DESAMBIGUACION:
            fila.tag = fila.candidatos[0]
            fila.ambiguo = False
            fila.candidatos = []
            nota_revision = "no se pudo desambiguar el equipo tras varios intentos: revisar a mano"
            fila.observacion = f"{fila.observacion} / {nota_revision}" if fila.observacion else nota_revision
        return False

    if fila.tag is None:
        # No se encontró ningún equipo: reinterpretar la respuesta como una nota nueva. Tope propio
        # de intentos (no solo el presupuesto de pasos entero de la nota): una frase que nombra una
        # pieza en vez de un equipo ("el rodamiento", "el pulmón") no va a resolver nunca por más
        # veces que se reinterprete igual, y cada reintento es una llamada al LLM — dejarlo
        # consumir todo `MAX_PASOS_LLM_POR_NOTA` (6) en esta sola fila fue lo que, contra el
        # corrector real, cortó la nota entera a los 90 s sin llegar a confirmarse (BUGS.md #26).
        intentos = estado.intentos_desambiguacion.get(indice, 0) + 1
        if _presupuesto_disponible(estado) and intentos <= MAX_INTENTOS_DESAMBIGUACION:
            filas_nuevas = interpretar_texto(
                texto_respuesta, fila.hora, fila.fotos, extractor, resolver, verificador_rango, estado.consumo
            )
            if filas_nuevas:
                estado.parte.filas[indice] = filas_nuevas[0]
            if estado.parte.filas[indice].tag is None:
                estado.intentos_desambiguacion[indice] = intentos
            return False
        # Se agotó el presupuesto o los intentos de desambiguación (ver BUGS.md #17 y #26): sin
        # más reinterpretaciones posibles, `fila.tag` se queda en `None` para siempre y
        # `_siguiente_fila_pendiente` la vuelve a preguntar sin parar. Se usa el nombre tal como lo
        # dijo el operador como tag literal (mismo fallback que ya usa `a_contrato()`) y se marca
        # para revisión manual, en vez de seguir preguntando.
        nota_revision = "no se pudo identificar el equipo: revisar a mano"
        fila.tag = fila.tag_dicho
        fila.observacion = f"{fila.observacion} / {nota_revision}" if fila.observacion else nota_revision
        return False

    if _es_confirmacion(texto_respuesta):
        fila.requiere_confirmacion = False
        fila.motivo_confirmacion = None
    elif _presupuesto_disponible(estado):
        filas_corregidas = interpretar_texto(
            texto_respuesta, fila.hora, fila.fotos, extractor, resolver, verificador_rango, estado.consumo
        )
        if filas_corregidas:
            # Esta respuesta corrige puntualmente la fila `indice` que se le preguntó: se
            # reemplaza directo, no hace falta adivinar cuál fila es por tag/tag_dicho (ver
            # BUGS.md #16 — la fusión genérica por texto dejaba filas viejas duplicadas cuando la
            # nueva extracción frasea el mismo equipo distinto a la nota original). Si la
            # respuesta menciona además otros equipos, esos sí se fusionan de la forma genérica.
            estado.parte.filas[indice] = filas_corregidas[0]
            _mergear_correccion(estado.parte, filas_corregidas[1:])
    else:
        # Se agotó el presupuesto de pasos del LLM para esta nota (4.1): dejar `requiere_confirmacion`
        # en True volvería a preguntar lo mismo por siempre (nunca se reinterpreta de nuevo, así que
        # nunca deja de ser "pendiente"). En cambio, se acepta el valor tal como está y se anota el
        # motivo en la observación, para que quede visible en el parte sin bloquear el diálogo.
        nota_revision = "no se pudo terminar de confirmar automáticamente (se agotó el presupuesto de pasos): revisar a mano"
        fila.observacion = f"{fila.observacion} / {nota_revision}" if fila.observacion else nota_revision
        fila.requiere_confirmacion = False
        fila.motivo_confirmacion = None
    return False
