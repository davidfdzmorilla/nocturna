# ADR 0002 · Entidades de dominio como dataclasses de la estándar

Fecha: 2026-09-16 · Estado: aceptado

## Contexto

`infrastructure/config.py` usa Pydantic para tipar la configuración del TOML. La salida de los agentes es JSON que T41–T43 necesitan validar: `Reading`, los tres niveles del Popularizer, la lista de decisiones del Editor. El equipo decidió usar Pydantic para esa validación (ADR 0001, implicado). La pregunta es dónde viven las **entidades de dominio** (`Item`, `Reading`, `Finding`, `Run`, `AgentCall`): ¿también en Pydantic, o en otro sitio?

Las reglas de negocio deben fallar de forma clara: "el Item no se puede descartar si ya está publicado" es un error de dominio, no un error de validación de datos. Pydantic no permite esa expresividad: su `ValidationError` dice qué campo fue, pero la lógica "esta transición de estado no es permitida" es algo que Pydantic no entiende.

## Decisión

Las **entidades de dominio viven como dataclasses de la estándar de Python**, sin Pydantic. Razones:

1. Las reglas de negocio lanzan excepciones de dominio (`InvalidTransition`, `InterestScoreOutOfRange`, `GuardedFieldAssignment`), no `ValidationError`.
2. Los **métodos de transición de estado** (`mark_read()`, `discard()`, `publish()`, `finish()`, `record_agent_call()`) encajan naturalmente en una clase con lógica, no en un modelo de validación declarativa.
3. Las **guardas sobre reasignación** (un campo de estado solo cambia a través de su método) se implementan con `__setattr__`, algo que Pydantic no contempla bien (habría que hacer un descriptor, y complicarse).
4. La capa `domain/` queda **sin dependencias de terceros**: solo estándar de Python. El dominio es puro y no carga librerías LLM, frameworks web ni SQL.

**Pydantic solo en las fronteras**:
- `infrastructure/config.py` carga el TOML tipado.
- `application/agents/` define esquemas Pydantic (`ReadingSchema`, `PopularizerSchema`, `EditorSchema`) que validan el JSON de los agentes.
- T11 traduce dataclass ↔ ORM a mano (SQLAlchemy no entra en dominio).

## Alternativas descartadas

- **Entidades como modelos Pydantic con `ConfigDict(validate_assignment=True)`**: la validación es reactiva al cambio, pero la semántica de "este campo solo cambia a través de este método" no tiene expresión natural en Pydantic; habría que hacer hacks con `field_validators` y `computed_fields` que harían el código más frágil.
- **Dejar Pydantic en todo**: entidades, configuración y esquemas en una sola librería. Simplificaría las conversiones, pero traería la semántica de Pydantic a decisiones de negocio puro (los 1–5 de `interest_score` no son "restricción de datos", son una regla de cómo Claude puntúa papers).
- **Herencia de una clase base Pydantic**: `BaseModel` atrae demasiada complejidad; dataclass puro es más predecible.

## Consecuencias

- **T11 traducirá dataclass ↔ modelo SQLAlchemy a mano**: campo a campo, iterando sobre los atributos de la entidad. Es código verboso pero trasparente; el ORM no se filtra hacia arriba y el dominio queda limpio.
- **T41–T43 validan dos veces**: primero Pydantic sobre el JSON (validación de datos), después la construcción de la entidad (validación de reglas de negocio). Es deliberado: dos responsabilidades distintas.
- Las excepciones de dominio se capturan por nombre, no por estructura de `ValidationError`. El stack trace es directo.
- `dataclasses.replace()` y `object.__setattr__` pueden saltarse las guardas; la defensa real es leer el estado acumulado desde la base de datos (T30), no fiarse de la memoria.
