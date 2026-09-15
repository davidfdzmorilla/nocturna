---
name: budget-guard-review
description: Lista de comprobación para revisar el control de gasto (BudgetGuard, ventana horaria, reserva del Editor, persistencia de tokens). Invocar en toda revisión que toque budget.py, LLMProvider o un agente.
---

# Revisión del control de gasto

El objetivo del control de gasto es que una noche mala del pipeline no deje al autor sin su suscripción durante el resto de la semana. Todo lo demás es secundario. Revisa esto en orden y cita fichero:línea en cada punto.

## 1. Camino único hacia el LLM

- `grep -rn "run_agent(" backend/src` → cada aparición debe estar dentro de un caso de uso que, en las líneas anteriores del mismo flujo, llame a `budget_guard.can_call(...)` y actúe sobre `False`.
- No hay llamadas a `query(` del SDK fuera de `infrastructure/llm/agent_sdk_provider.py`.
- `nocturna run-item` (depuración) también pasa por `BudgetGuard`. "Es solo para depurar" no es excepción.

## 2. Ventana horaria

- `can_call` comprueba la hora actual contra `window.hard_stop` **en cada llamada**, no solo al inicio del Run.
- La hora se lee de un reloj inyectable (`Clock` en `domain/`) para poder testearla. Nada de `datetime.now()` suelto en `application/`.
- Pasado `hard_stop`: lo que esté en vuelo se cancela (`asyncio` cancel / timeout), el Run pasa a `killed`, y no se llama al Editor.

## 3. Reserva del Editor

- `budget.editor_reserve_tokens` se resta del presupuesto disponible para Reader y Popularizer desde el principio de la noche, no "cuando se acerque el final".
- El Editor se llama como máximo una vez por Run: hay un contador persistido o una comprobación sobre `AgentCall` con `agent = editor` y ese `run_id`.
- Si el presupuesto sin reserva se agota: Run `partial`, Editor **no** se llama, nada se publica.

## 4. Persistencia

- Los tokens de cada llamada se escriben en `AgentCall` y se suman a `Run.tokens_used` en la misma transacción, **antes** de que el caso de uso devuelva el resultado.
- `can_call` lee el acumulado de base de datos (o de un valor cargado de base de datos al inicio y actualizado en la misma transacción), no de una variable de módulo.
- Test de reinicio: un Run a medias, proceso reiniciado, el acumulado sigue ahí.

## 5. Contadores secundarios

- `max_items_per_night` se aplica en la selección de ítems, no después de haberlos leído.
- `max_turns_per_agent` llega al SDK como `max_turns`.
- `item_timeout_s` y `run_timeout_s` existen y se aplican con `asyncio.wait_for` o equivalente.

## 6. Modo de fallo

- Un error del SDK que indique límite de uso alcanzado termina la noche (`partial`), no se reintenta.
- JSON inválido: un reintento como máximo, y el reintento también cuenta tokens y pasa por `can_call`.
- Ningún `except` amplio alrededor de la llamada al LLM que trague `BudgetExceeded` u `OutsideExecutionWindow`.

## 7. Configuración

- Todos los valores anteriores vienen de `config/pipeline.toml` vía el objeto de configuración tipado. `grep -rn "300_000\|300000\|04:45" backend/src` no debe devolver nada fuera de la carga de configuración o de los defaults documentados.

Veredicto: cualquier fallo en los puntos 1–4 es **bloqueante**. En la segunda pasada, repite solo 1–4 leyendo el código completo, no el diff.
