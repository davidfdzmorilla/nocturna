# ADR 0003 · Persistencia: tipos, fronteras transaccionales y controles de concurrencia

Fecha: 2026-09-16 · Estado: aceptado

## Contexto

T11 implementa la persistencia con SQLAlchemy 2, Alembic y PostgreSQL 16. El modelo de dominio (`domain/entities.py`) está limpio de ORM; T11 traduce dataclass ↔ ORM a mano en `infrastructure/db/mappers.py`, y los repositorios en `infrastructure/db/repositories.py` son los únicos consumidores de las filas. El stack es íntegramente síncrono (SQLAlchemy sync es lo instalado y el pipeline es batch monohilo). Varias decisiones de arquitectura tienen consecuencias en el control de gasto: `AgentCall` y la actualización de `Run.tokens_used` deben viajar en la misma transacción, y dos noches solapadas gastarían el doble del presupuesto semanal.

## Decisión

### 1. `unit_of_work` como única frontera transaccional

El módulo `infrastructure/db/session.py` define un `@contextmanager` llamado `unit_of_work` que es el **único lugar del proyecto donde se llama a `Session.commit()` o `Session.rollback()`**. Los repositorios (`infrastructure/db/repositories.py`) nunca lo hacen.

**Consecuencia**: un `AgentCall` y la actualización de `Run.tokens_used` (ambos escritos por `application/` en la misma unidad de trabajo) caen forzosamente en la misma transacción. Si un repositorio confirmara su propia transacción, el acumulado de gasto podría perder una llamada ya cobrada. El control de gasto (`application/budget.py` en T30) depende de esta invariante.

### 2. `Run.tokens_used` como caché desnormalizada

La columna `runs.tokens_used` no es la fuente de verdad del gasto de la noche. La fuente es la **suma de las filas de `agent_calls`** (`AgentCallRepository.tokens_used_for_run`).

El método `RunRepository.save()` persiste lo que traiga el objeto `Run` en memoria, incluido un valor manipulado o desincronizado. No lo recalcula ni lo valida contra la base.

**Consecuencia**: T30 **nunca** lee `Run.tokens_used` en memoria para autorizar una llamada. Lee siempre `AgentCallRepository.tokens_used_for_run()` desde la base de datos. La caché es un registro histórico; la decisión de gasto la toma la base de datos.

### 3. Índice único parcial para impedir dos `Run` en `running` a la vez

La tabla `runs` tiene un índice único parcial:

```sql
CREATE UNIQUE INDEX uq_runs_status_running ON runs(status) 
WHERE status = 'running'
```

Dos noches solapadas leerían cada una su `Run.tokens_used_for_run` y autorizarían gasto duplicado sin darse cuenta.

**Consecuencia documentada**: un `Run` huérfano de un proceso muerto sin cerrar bloquea el arranque siguiente con `IntegrityError`. La política de recuperación (retomar con `current()` o cerrarlo como `killed` y abrir uno nuevo) se decide en T30.

### 4. Enums como `VARCHAR` + `CHECK`, no tipo nativo

Todas las columnas de enum (`item_status`, `run_status`, `agent_call_status`, `agent_role`, `finding_type`) usan `sa.Enum(..., native_enum=False, create_constraint=True)` con `values_callable` explícita.

`native_enum=False` las materializa como `VARCHAR` con `CHECK` en PostgreSQL, no como tipo `ENUM` nativo. Esto es deliberado por:

- **Reversibilidad de migraciones**: un `CHECK` se puede añadir o quitar con `ALTER TABLE`. Un tipo `ENUM` nativo requiere `ALTER TYPE` aparte y es propenso a fallos.
- **Valores abiertos**: tres conjuntos de valores (item_status, run_status, agent_call_status) son decisión abierta en fase 1. Cambiar un `CHECK` es una migración reversible; quitar un valor de un `ENUM` nativo no lo es.

**Filo encontrado en T11**: `sa.Enum(..., native_enum=False)` **no crea el `CHECK` por defecto en SQLAlchemy 2.0** sin pasarle explícitamente `create_constraint=True`. Sin él, las columnas aceptaban cualquier cadena — se detectó insertando un valor inventado que la base aceptó silenciosamente. El nombre del constraint sigue la `NAMING_CONVENTION` del módulo: `ck_<tabla>_<name>`.

`values_callable` es obligatorio: sin él, SQLAlchemy persiste el *nombre* del miembro del enum (`"NEW"`) en lugar de su *valor* (`"new"`), que es lo que espera la API de lectura (T50) y el resto del dominio.

### 5. `ARRAY(Text)` en lugar de JSONB

Las columnas `categories` (Item), `objects` (Reading) y `claims` (Reading) usan `postgresql.ARRAY(sa.Text)`, no JSONB.

Son listas homogéneas de cadenas simples, sin estructura anidada. PostgreSQL valida el tipo de elemento en cada inserción; ninguna consulta de fase 1 mira dentro de la lista (no hay `@>` o `?` en las queries).

**Consecuencia**: más compacto que JSONB, validación de tipo nativa, y sin índice GIN (fase 1 no lo necesita).

### 6. Sin `relationship()` en ninguna dirección

Los modelos ORM (`ItemRow`, `ReadingRow`, `FindingRow`, `RunRow`, `AgentCallRow`) no tienen decoradores `@relationship()` que expongan las relaciones de FK como atributos navegables.

Las relaciones perezosas del ORM son el camino más fácil por el que SQLAlchemy se filtra hacia `application/`. Los repositorios navegan por id explícito (`session.get()` o `select(...).where(...)`), sin traversals automáticos.

**Consecuencia**: ningún repositorio carga accidentalmente historiales completos. Las consultas son predecibles y explícitas.

### 7. UUID generado en dominio, sin `server_default` en timestamps

La identidad de cada entidad (`id`) es un `UUID` generado por el constructor de la clase de dominio (`uuid4()` en `domain/entities.py`) y copiado por el mapper a la fila ORM. No hay `default=uuid4` ni `server_default=gen_random_uuid()` en el modelo SQLAlchemy.

Si la fila generara su propio id, el objeto en memoria y la fila persistida tendrían identidades distintas hasta el siguiente `refresh()`. El mapper debe ser el único responsable de `id`.

Lo mismo aplica a los timestamps: `published_at`, `fetched_at`, `started_at`, etc. no tienen `server_default`. El instante lo decide el dominio. Por ejemplo, `Run.started_at` es el instante que vio `BudgetGuard` (en T30) al comprobar la ventana de ejecución; si la base de datos lo pusiera al `INSERT`, ambos podrían no coincidir.

### 8. `alembic.ini` sin `sqlalchemy.url`

El fichero `alembic.ini` no contiene `sqlalchemy.url`. En su lugar, la función `env.py` lee de `Settings.database_url` en tiempo de ejecución.

La razón: `alembic.ini` se procesa con interpolación de `configparser` estándar, así que un `%` en una contraseña rompería la cadena de forma opaca. La URL sale de las variables de entorno y `Settings`, bajo control de código.

## Alternativas descartadas

- **`relationship()` para facilitar las queries**: complicaría accidentalmente el código de aplicación; explícito es mejor que implícito.
- **Tipo `ENUM` nativo**: mejor performance en teoría, pero menos reversible. Fase 1 debe permitirse cambiar de opinión.
- **JSONB para listas**: innecesario; `ARRAY` es más compacto para el caso de uso de fase 1.
- **Múltiples transacciones, una por repositorio**: `Run.tokens_used` y `AgentCall` podrían caer en transacciones distintas; un fallo entre medias dejaría el acumulado corto.
- **Generar id en la base de datos**: el mapper necesitaría hacer un `refresh()` o un `SELECT RETURNING` después de cada insert; complicado e innecesario.

## Consecuencias

- **T30 debe leer `AgentCallRepository.tokens_used_for_run()` desde la base de datos**, nunca el campo en memoria de `Run`. La dokumentación de `RunRepository.save()` lo marca explícitamente como caché.
- **Las migraciones Alembic son el único camino para cambiar el esquema**; no hay "scripts de inicialización" que salten constraint checks.
- **Un `Run` muerto sin cerrar bloquea la noche siguiente** hasta que se resuelva manualmente o con una política de recuperación (T30).
- **La concurrencia de dos processes Python escribiendo simultáneamente en la misma base se detecta rapidamente** (violación del índice único parcial) y fuerza cierre ordenado o reintentos.
