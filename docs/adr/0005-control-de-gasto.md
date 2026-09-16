# ADR 0005 · Control de gasto

Fecha: 2026-09-16 · Estado: aceptado

## Contexto

T30 implementa `BudgetGuard`, la puerta de control de gasto de la suscripción Claude Max personal. `CLAUDE.md` llama a esto "no negociable": un bucle descontrolado a las 3 de la mañana puede agotar la cuota semanal y dejar al autor sin Claude durante días. El control debe ser imposible de ignorar por accidente, y los motivos de denegación deben distinguir entre "se cruzó `hard_stop`" (`killed`) y "se acabó el presupuesto" (`partial`), porque son cierres de noche distintos.

T30 afronta decisiones de arquitectura que afectan a T44 (orquestador), T60 (calibración), y al monitoreo del gasto.

## Decisión

### 1. Arquitectura: `check` + `authorize`

El alcance original de `PLAN_TAREAS.md` decía `can_call(agent, estimated_tokens) -> bool`. Un booleano no transporta el motivo. Se reemplaza con dos métodos:

- **`check(role, estimated_tokens) -> BudgetDecision`**: devuelve `allowed: bool` y `reason: DenyReason | None` (obligatorio si `allowed=False`), además de `remaining_tokens` para telemetría. Define `__bool__` para que una decisión positiva sea truthy.
- **`authorize(role, estimated_tokens)`**: sin retorno. Llama a `check` y lanza la excepción correspondiente si se deniega.

**Motivo**: `check` es una consulta sin efectos; `authorize` es la barrera imposible de ignorar (genera una excepción de verdad, no un booleano que alguien podría pasar por alto). El motivo (`DenyReason`) permite que `terminal_status_for(reason)` traduzca "fuera de ventana" → `KILLED`, "presupuesto agotado" → `PARTIAL`, sin ambigüedad. Llamadas desde tests pueden usar `check` + `if decision:`, mientras que el orquestador (T44) llama a `authorize` y nunca tiene que preguntar "¿me autorizan?".

### 2. Sin estado mutable en el guard

`BudgetGuard` **no cachea nada**. Cada `check` relee `RunRepository` y `AgentCallRepository`. Dos instancias construidas contra la misma base de datos deciden lo mismo. El acumulado sobrevive a un reinicio del proceso.

**Motivo**: la alternativa (memoizar el gasto acumulado en memoria) es exactamente el camino por el que un contador desincronizado autoriza más gasto del que existe en la base de datos. Con seis meses de operación en producción, un reinicio coincidente con una noche de alto gasto dejaría un gap. El coste es un `SELECT` por llamada; es asumible.

### 3. El acumulado sale de `AgentCallRepository.tokens_used_for_run`, nunca de `Run.tokens_used`

Cada llamada autorizada persiste una fila en `agent_calls`. `tokens_used_for_run(run_id)` suma `tokens_in + tokens_out` de esas filas desde la base de datos. Las dos mitades cuentan: `CLAUDE.md` fija el tope sobre "tokens (entrada + salida) por noche", y en este pipeline la entrada domina —un abstract completo en el prompt— así que ignorarla multiplicaría el gasto real frente al configurado.

**Nunca** se lee `run.tokens_used` del objeto en memoria para tomar decisiones de gasto. Ese campo es una caché desnormalizada (ADR 0003); la fuente de verdad es la tabla `agent_calls`.

**Motivo**: ver ADR 0003 § 2. El campo en memoria puede estar desincronizado o manipulado; la base de datos no miente.

### 4. Orden de comprobación: `RUNNING` → ventana → llamadas → presupuesto

`check` verifica en este orden:

1. ¿El `Run` existe y está en estado `RUNNING`? Si no → `RUN_NOT_RUNNING`.
2. ¿Estamos dentro de la ventana `[start, hard_stop)`? Si no → `OUTSIDE_WINDOW`.
3. ¿El rol ya alcanzó su tope de llamadas? Para el Editor, `max_editor_calls_per_night`; para Reader y Popularizer, `max_calls_per_item * max_items_per_night`. Si sí → `EDITOR_ALREADY_CALLED` o `CALL_LIMIT_REACHED` respectivamente.

   No confundir con `max_turns_per_agent`, que es otra cosa: el número de turnos que T40 pasará al SDK **dentro de una sola llamada**, no el número de llamadas.
4. ¿El presupuesto disponible (con la reserva del Editor ya restada) cubre la estimación? Si no → `BUDGET_EXHAUSTED`.

**Motivo**: la ventana va antes que el presupuesto porque el motivo determina el estado terminal. "Fuera de ventana" significa que `hard_stop` se cruzó, así que el `Run` cierra como `KILLED`; "presupuesto agotado" es un `PARTIAL`. Si el presupuesto corriera primero, un `BudgetExceeded` lanzado a las 04:45 se traduciría como `PARTIAL` cuando debería ser `KILLED`. La ambigüedad rompe la invariante de `CLAUDE.md`.

### 5. La reserva del Editor se resta desde el minuto cero

Reader y Popularizer ven un presupuesto **reducido** desde la primera llamada de la noche: `nightly - editor_reserve_tokens`. El Editor ve el presupuesto completo.

Método `_available_tokens(run, role)` implementa esto; `_remaining(run, role)` lo consulta al calcular tokens sobrantes.

**Motivo**: la reserva no es "algo que se resta si queda poco tiempo". Es una garantía: sin importar cuántos tokens gaste Reader/Popularizer, el Editor siempre tendrá su reserva disponible si se le llama (cosa que podría no pasar si agotamos el presupuesto en Reader). Esto es lo conservador.

### 6. `estimated_tokens <= 0` lanza `ValueError`

```python
if estimated_tokens <= 0:
    raise ValueError(f"estimated_tokens debe ser mayor que cero, recibido {estimated_tokens}")
```

Se rechaza antes de cualquier lectura de base de datos.

**Motivo**: pasar `0` convertiría al guard en un pasapuertas, autorizando una llamada "gratis" incluso cuando el presupuesto está exacto. La primera violación de eso nunca se nota porque la llamada sí gasta tokens; la denegación llega cuando el contador está fuera de sync.

### 7. `BudgetDenied` no hereda de `DomainError`

```python
class BudgetDenied(Exception):  # NO de DomainError
    """..."""
```

Las excepciones específicas (`BudgetExceeded`, `OutsideExecutionWindow`, etc.) heredan de `BudgetDenied`.

**Motivo**: en T44 o en los casos de uso de agentes (T41–T43), un `except DomainError` amplio es tentador para capturar "cualquier error del negocio". Una denegación de presupuesto **no es un error del negocio**: es un veto del sistema que no debe pasar desapercibido. Quien quiera capturar denegaciones tiene que nombrarlas explícitamente: `except BudgetDenied`.

### 8. `record_call` valida pertenencia y maneja `RunAlreadyFinished`

Cuando se registra una llamada hecha:

```python
def record_call(self, call: AgentCall, run: Run) -> None:
    if run.id != self._run_id or call.run_id != self._run_id:
        raise InvariantViolation("...")
    self._agent_calls.add(call)
    try:
        run.record_agent_call(call)
    except RunAlreadyFinished:
        return
    self._runs.save(run)
```

**Dos validaciones**:

1. `call` y `run` deben pertenecer al `Run` vigilado por este guard. Previene que un guard para `run_id=A` contabilice gasto de otro `run_id` por accidente.
2. Si el `Run` ya está cerrado (`RunAlreadyFinished`), el `AgentCall` ya está en la sesión (línea anterior), así que sobrevive **si esa unidad de trabajo llega a hacer `commit`**. La fuente de verdad (regla 3) es `agent_calls`, no `Run.tokens_used`.

   **Alcance exacto de esa captura, que T44 hereda**: capturar `RunAlreadyFinished` evita que *esa excepción concreta* provoque el rollback. No protege contra un rollback por cualquier otra causa dentro del mismo `unit_of_work`, y el camino de cancelación de las 04:45 es justamente el candidato a lanzar otra cosa. T44 debe contabilizar la llamada en vuelo en su propia `unit_of_work`, separada del camino de cancelación. `Run.tokens_used` queda un incremento corto en este caso: ninguna decisión del guard se ve afectada, pero los informes de T60 deben recalcular desde `agent_calls`.

**Motivo**: un `AgentCall` representa tokens que la suscripción **ya consumió** en el momento que se invoca este método. Si esos tokens no se registran, el acumulado de la noche se queda corto y futuras decisiones de gasto serán optimistas. El caso más real: T44 cierra el `Run` como `KILLED` cuando cruza `hard_stop`, pero una llamada lanzada antes del corte aún está en vuelo. Cuando vuelve, el `Run` ya no acepta nuevas llamadas; sin capturar `RunAlreadyFinished`, la excepción haría rollback y los tokens desaparecerían del histórico.

### 9. `max_editor_calls_per_night = 2` contando intentos, no aciertos

El Editor se llama como máximo 2 veces por noche (por defecto). El contador de `AgentCallRepository.count_for_run(run_id, EDITOR)` cuenta **todas** las filas con `status` en (`ok`, `invalid_output`, `error`, `timeout`). 

Agotar el tope significa: se intentó `max_editor_calls_per_night` veces, no que se completaron exitosamente. Esto preserva "un Editor por noche" en su sentido real y permite un reintento si la primera respuesta es JSON inválido.

**Motivo**: el gasto (tokens de la suscripción) sale del **intento**, no del éxito. Si contáramos solo las llamadas con `status = ok`, un Editor cuya primera respuesta fuera JSON inválido (error de negocio, no del LLM) perdería el reintento. Además, los intentos fallidos también consumen tokens al LLM: un timeout es un timeout, la suscripción pagó.

### 10. `timeout_for_call()` devuelve `min(item_timeout_s, seconds_until_hard_stop())`

Una llamada individual nunca sobrevive a `hard_stop`. El timeout pasado a `LLMProvider.run_agent` es el menor de:

- `item_timeout_s` (configurado en `pipeline.toml`).
- Segundos que quedan hasta `window.hard_stop`.

**Motivo**: a `hard_stop` exacto, este valor es 0, así que quien lo reciba debe tratarlo como "no hay tiempo, no llames". A 10 segundos antes de `hard_stop`, el LLM tiene 10 segundos máximo. La orquestación (T44) usa esto para no iniciar llamadas que van a expirar.

### 11. `seconds_until_hard_stop` usa UTC en la resta, no hora de pared

```python
def seconds_until_hard_stop(now: datetime, start: time, hard_stop: time) -> int:
    # ...
    remaining = (hard_stop_dt.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    return max(int(remaining), 0)
```

Ambos operandos se convierten a UTC antes de restar. **Nunca** se restan en la hora de pared de `now.tzinfo`.

**Motivo descubierto en revisión**: Python resta dos `datetime` con el mismo `tzinfo` en hora de pared, ignorando `utcoffset()`. En una noche de cambio de hora entre `now` y `hard_stop_dt`, esto introduce un error de ±3600 segundos. Ejemplo: en la noche del adelanto de hora (spring forward), la resta devuelve 11700 s cuando debería ser 8100 s, dando una hora de margen ficticio contra el corte de las 04:45. En la del atraso (fall back), lo contrario. La conversión a UTC de ambos operandos antes de restar garantiza una resta sobre instantes reales, inmune a cambios de `utcoffset()`.

Dos tests que decían cubrir esto no lo detectaban porque situaban la hora **después** de la transición, donde hora de pared y tiempo real coinciden. Con la corrección, ahora fallan intencionadamente sin ella.

### 12. `window.timezone` como clave IANA explícita en `pipeline.toml`

```toml
[window]
start = "00:00"
hard_stop = "04:45"
timezone = "Europe/Madrid"  # clave IANA, no TZ del sistema
```

`SystemClock` se construye en `cli.py` (composition root) con `ZoneInfo(config.window.timezone)`.

**Motivo**: la hora local del sistema depende de `TZ`, que:

- `cron` no siempre propaga.
- Un contenedor Docker en fase 2 sitúa en UTC por defecto.
- El autor puede cambiar `TZ` sin tocar el código.

Si no se especifica explícitamente, un `hard_stop` de 04:45 se dispararía a 06:45 locales (offset +2 en verano, por ejemplo), cruzando medianoche y abriendo una segunda sesión de cinco horas que `CLAUDE.md` prohíbe. Con `window.timezone` como clave, la verificación es explícita en el código y la configuración la respalda.

### 13. `BudgetPolicy` es `dataclass` estándar, traducción en `cli.py`

`application/budget.py` define:

```python
@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    nightly_tokens: int
    editor_reserve_tokens: int
    # ...
```

**No es Pydantic**. La validación del TOML ya la hizo `PipelineConfig` en `infrastructure/config.py`. La traducción `PipelineConfig → BudgetPolicy` vive en `cli.py` (composition root) en la función `budget_policy_from_config`.

`application/budget.py` no importa `infrastructure/`.

**Motivo**: evitar validación redundante. `BudgetPolicy` recibe valores que ya pasaron el filtro de Pydantic; revalidarlos es trabajo inútil. Además, mantiene `application/` desacoplada de infraestructura, preservando la flecha arquitectónica `application → domain`.

### 14. Función pura `is_within_window` cubre tanto ventanas normales como que cruzan medianoche

```python
def is_within_window(now: datetime, start: time, hard_stop: time) -> bool:
    """¿Cae `now` dentro de `[start, hard_stop)`?"""
    t = now.time()
    if start <= hard_stop:
        return start <= t < hard_stop
    return t >= start or t < hard_stop
```

Cubre:

- **Ventana normal** (`00:00–04:45`, la real de fase 1): dentro si `start ≤ t < hard_stop`.
- **Ventana que cruza medianoche** (`22:00–02:00`): dentro si `t ≥ start` (noche anterior a medianoche) o `t < hard_stop` (después de medianoche).

Cerrada por abajo, abierta por arriba en ambos casos.

**Motivo**: pura, sin efectos. Facilita tests y reutilización en `cli.py` para el plan de gasto de `--dry-run`.

### 15. `effective_nightly_tokens` aplica el multiplicador del reset semanal una sola vez

```python
def effective_nightly_tokens(policy: BudgetPolicy, now: datetime) -> int:
    if now.weekday() == policy.weekly_reset_weekday and now.hour >= policy.weekly_reset_hour:
        return int(policy.nightly_tokens * policy.reset_day_multiplier)
    return policy.nightly_tokens
```

La noche del reinicio semanal de la suscripción Claude Max, si la hora local es ≥ `weekly_reset_hour`, el presupuesto se multiplica por `reset_day_multiplier` (por defecto 1.0, inerte). Solo esa noche; las demás, presupuesto normal.

El `Run` lee el valor efectivo al crearse (T44) y lo asigna a `run.budget_tokens`.

**Motivo**: el multiplicador se aplica una única vez, en el origen. Cualquier decisión de `BudgetGuard` usa `run.budget_tokens`, que ya tiene el multiplicador aplicado. Si `BudgetGuard` lo aplicara cada vez que se consulta, una noche de reset vería el multiplicador una sola vez al iniciar, pero el presupuesto cambiaría según la hora exacta de cada llamada.

La reserva del Editor **no escala** con el multiplicador: si una noche de reset el presupuesto sube a 600k (con `reset_day_multiplier=2`), la reserva sigue siendo 60k (nominal). Esto es lo conservador; calibración en T60.

## Alternativas descartadas

- **`can_call(agent, estimated_tokens) -> bool`**: un booleano no transporta motivo. Es lo que decía el alcance original; causa confusión cuando T44 necesita distinguir `killed` de `partial`.
- **Memoización del acumulado en memoria**: rompe ante reinicio a media noche. Un selector de `AGENT_CALL_COUNT` y una suma local te dejan fuera de sync una hora después de un reinicio.
- **Leer `Run.tokens_used` para autorizar**: `Run.tokens_used` es una caché desnormalizada; depender de ella es ir contra ADR 0003 § 2 y las restricciones de control de gasto.
- **Presupuesto de `hard_stop` antes que `RUNNING`**: un `Run` no existe hasta que se crea; si no se comprueba primero, `get(run_id)` devuelve `None` y la denegación es un `RUN_NOT_RUNNING` válido, pero perder el motivo de "fuera de ventana" hace que T44 no cierre como `KILLED`.
- **Reserva del Editor aplicada bajo demanda**: "solo restarla cuando se acerca el final del presupuesto" pierde la garantía. Es más simple restarla desde el minuto cero.
- **Aceptar `estimated_tokens = 0`**: convertiría el guard en un pasapuertas hasta el límite exacto.
- **`BudgetDenied` hereda de `DomainError`**: expone la denegación a ser capturada accidentalmente con `except DomainError`.
- **No capturar `RunAlreadyFinished`**: el `AgentCall` ya está en la sesión, pero el rollback lo deshace. Los tokens gastados desaparecen.
- **Contar solo Editor llamadas exitosas (`status = ok`)**: un JSON inválido en la primera respuesta pierde el reintento.
- **Hora de pared para `seconds_until_hard_stop`**: cambios de hora introducen ±3600 s de error.
- **Leer `TZ` del sistema en `SystemClock`**: la hora local varía con contenedores y crons; imposible de depurar a distancia.
- **Pydantic para `BudgetPolicy`**: duplica validación que ya pasó `infrastructure/config.py`.

## Consecuencias

- **T30 está integrada en la arquitectura de T44** (orquestador): cada `run_agent` pasa por `authorize` primero. Nada puede llamar a LLM sin pasar por el guard.
- **No hay segundo contador de gasto en memoria.** La única fuente es `agent_calls` en la base de datos. Reinicio del proceso a media noche no desincroniza el acumulado.
- **Cambios de hora no rompen el `hard_stop`.** La resta de segundos usa UTC.
- **La noche de reset semanal puede tener más presupuesto, pero la reserva del Editor no escala.** Se calibra en T60.
- **El tope del Editor es 2 intentos por noche por defecto.** Eso cubre "la llamada + el reintento por JSON inválido".
- **`terminal_status_for` no acepta `EDITOR_ALREADY_CALLED`:** ese motivo no cierra la noche; es responsabilidad de T44 decidir cuándo termina.
- **`BudgetDenied` no se mezcla con `DomainError`.** Los test y código que captura excepciones deben ser explícitos.
