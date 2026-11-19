"""Copia parcial de `libreria-tp/planta_kit` (librería de la cátedra, Academia Novit 2026), sin cambios.

Solo los módulos determinísticos que usa el parte por voz (Lote 5 de la verificación final):
`numeros`, `nombres`, `unidades`, `notas` (parser de notas), `respuestas` (respuestas del operador),
`catalogo` y `seguridad` (la importa `notas`). Se copian en vez de instalarse porque la imagen solo
ve este repo como contexto de build. Los ajustes propios (mañas de Whisper, mapeo a nuestras filas)
están en `sistema/procesamiento/parser_notas.py`, no acá.

Diseño de la cátedra (`libreria-tp/ADLC.md`, CU1): "Workflow + LLM puntual: el LLM solo extrae
cuando el parser no alcanza".
"""

__version__ = "0.1.0"
