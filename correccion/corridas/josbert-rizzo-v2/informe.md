# Corrección · josbert-rizzo

**Nota: 63.4 / 100 · APRUEBA** (mínimo 60)

Se midieron criterios por 63 de 93 puntos; la nota está reescalada a 100.

Escenarios sin datos: tormenta-perfecta, sin-luz, contratista, ataque, libre

## Puntaje

| Criterio | Peso | Valor | Puntos |
|---|---:|---:|---:|
| **A. Parte por voz** | | | |
| A1 Cobertura | 5 | 100% | 5.0 |
| A2 Borrador al primer intento | 10 | 6% | 0.56 |
| A3 Parte final correcto | 8 | 6% | 0.44 |
| A4a Pregunta cuando hace falta | 4 | 100% | 4.0 |
| A4b No pregunta de más | 3 | 64% | 1.93 |
| **B. Detección en planta** | | | |
| B1 Sin novedad / Escalado | 10 | 65% | 6.53 |
| B2 Alerta de seguridad | 10 | 100% | 10.0 |
| **D. Órdenes de trabajo** | | | |
| D1 Reglas de aprobación | 15 | sin datos | – |
| D2 Orden adecuada a la falla | 10 | sin datos | – |
| **E. Operación sin enlace** | | | |
| E1 Sigue andando sin enlace | 3 | 100% | 3.0 |
| E2 Pendientes visibles | 3 | 50% | 1.5 |
| E3 Sin duplicados | 4 | 100% | 4.0 |
| **F. Robustez** | | | |
| F1 Mensajes válidos | 2 | 100% | 2.0 |
| F2 Latido | 1 | 100% | 1.0 |
| F3 Sobrevive al corte de luz | 2 | sin datos | – |
| **G. Cierre y certificación** | | | |
| G1 Orden ejecutada | 3 | sin datos | – |
| G2 Cierre propio | 2 | inactivo | – |
| G3 Certificación de la contratista | 5 | inactivo | – |

## Entrega

- README.md: sí
- docker-compose.yml: sí
- tests: sí

## Detalle por escenario

- A2 · martes-tranquilo: 11% — {"notas": 9, "confirmados_al_primero": 1}
- A2 · fuga-sin-enlace: 0% — {"notas": 4, "confirmados_al_primero": 0}
- A3 · martes-tranquilo: 11% — {"notas": 9, "sin_confirmar": ["NOTA-00002", "NOTA-00003", "NOTA-00005", "NOTA-00008", "NOTA-00006", "NOTA-00010", "NOTA-00011", "NOTA-00013"]}
- A3 · fuga-sin-enlace: 0% — {"notas": 4, "sin_confirmar": ["NOTA-00001", "NOTA-00006", "NOTA-00004", "NOTA-00005"]}
- A4b · martes-tranquilo: 29% — {"claras": 7, "preguntas_de_mas": ["NOTA-00002", "NOTA-00003", "NOTA-00005", "NOTA-00006", "NOTA-00010"]}
- B1 · martes-tranquilo: 56% — {"decisiones": 18, "errores": [{"nota": "NOTA-00002", "equipo": "F-201A", "esperado": "SinNovedad", "publicado": ["Escalado"]}, {"nota": "NOTA-00002", "equipo": "B-102", "esperado": "SinNovedad", "publicado": []}, {"nota": "NOTA-00003", "equipo": "B-101", "esperado": "SinNovedad", "publicado": []}, {"nota": "NOTA-00003", "equipo": "F-201B", "esperado": "SinNovedad", "publicado": []}, {"nota": "NOT
- B1 · fuga-sin-enlace: 75% — {"decisiones": 4, "errores": [{"nota": "NOTA-00005", "equipo": "B-101", "esperado": "SinNovedad", "publicado": []}]}
- E2 · fuga-sin-enlace: 50% — {"avisa_pendientes_en_el_corte": false, "queda_en_cero_despues": true}
