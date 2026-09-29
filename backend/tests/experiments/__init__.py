"""Soporte del experimento T71.b: un prompt de Reader experimental que, además
de la salida normal (`ReaderOutput`), extrae medidas numéricas atribuidas a un
planeta concreto (`reader_attribution.py`).

No es un doble de `LLMProvider` (esos viven en `tests/fakes/`) ni una ayuda
para construir mensajes del SDK (`tests/helpers/sdk_doubles.py`): es el código
de un experimento de un solo uso (T71.b), con su propio prompt versionado
(`reader-measures-exp1.md`) y su propio esquema de salida
(`ExperimentalReaderOutput`), deliberadamente fuera de `application/agents/`
porque no es parte del pipeline de producción -- ver `docs/PLAN_TAREAS.md`,
T71.
"""
