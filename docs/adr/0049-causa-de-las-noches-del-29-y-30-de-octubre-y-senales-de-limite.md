# ADR 0049 · Causa de las noches del 10-29 y 10-30 y señales de límite

Fecha: 2026-11-07 · Estado: aceptado · Tarea: T111 · Relacionados: ADR 0011, 0044, 0045

## Contexto

ADR 0045 (contexto y decisión 3) y la OD de T105 (decisión 6) atribuían las noches del 2026-10-29 y 2026-10-30 a un 429 de la suscripción que el proveedor recibió como `agent_error`. La atribución era incompleta: cada noche tuvo una causa distinta. Los hechos salen de las transcripciones del CLI, porque los logs propios no guardaban el motivo del fallo:

- **2026-10-29: límite semanal.** El SDK emitió un `AssistantMessage` con `error="rate_limit"` y un `ResultMessage` con `is_error=True` y `api_error_status=429`. El proveedor ya lo reconocía como `LLMRateLimited` y el Reader cortó con `end=rate_limited_reader`.
- **2026-10-30: autenticación caducada.** `AssistantMessage` con `error="authentication_failed"` y sin `api_error_status`. Llegó como `agent_error` (5 llamadas, 0 tokens) y cortó el cortacircuitos. No es un límite de tasa.
- **Hueco real.** Si tras un `ResultMessage` con 429 llega otro mensaje del CLI, el SDK lanza `ProcessError`. Las ramas `ClaudeSDKError` y `Exception` del proveedor no miraban el 429 y lo clasificaban como `LLMError`.

ADR 0045 §3 describe ambas noches como `circuit_breaker`. Este ADR corrige la causa del 10-30. La clasificación del 10-29 en el informe depende de la nota del `Run` ya escrita; no se reescribe. ADR 0021 (sección de datos del archivo, línea de noches `partial`) atribuye las dos noches al 429; tampoco se edita (publicado).

## Decisión

1. **El 429 se reconoce en todas las ramas de error por campos tipados:** `ResultMessage.api_error_status == 429`, solo si `is_error`, o `AssistantMessage.error == "rate_limit"`. Sin comparar texto. `_llm_error_class` elige entre `LLMRateLimited` y `LLMError`.
2. **Las señales solo cambian la clase de un error que ya ocurre.** Nunca convierten un éxito en fallo ni alteran los tokens contabilizados.
3. **`RateLimitEvent` solo se registra.** No decide la clase (OD T111 D2).
4. **Log `llm.call_failed` (WARNING), una línea por fallo, con campos fijos:** `event`, `role`, `item_id`, `error_class`, `sdk_exception`, `subtype`, `is_error`, `api_error_status`, `terminal_reason`, `assistant_error`, `rate_limit_status`, `rate_limit_type`, `rate_limit_resets_at`, `exit_code` (entero o nulo). Nunca `result`, `errors`, `stderr`, `session_id`, el prompt ni `str(exc)`: pueden llevar texto del modelo o del abstract.
5. **La autenticación queda fuera de T111.** `authentication_failed` sigue siendo `LLMError` y aparece en el log como `assistant_error`. Detectarla, cortar en la primera llamada y avisar en el informe es T112 (OD T111 D1).

## Alternativas descartadas

- **Heurística sobre el texto de los errores o de los logs.** Frágil entre versiones del CLI y puede exponer contenido con secretos (ya descartada en ADR 0045).
- **Que `RateLimitEvent` con `status = rejected` decida `LLMRateLimited`.** Un evento de tasa no garantiza que la llamada fallara. Se registra como dato de diagnóstico (OD T111 D2).
- **Detectar la autenticación en T111.** Cambia la reacción del runner y el informe. Va en T112 por separado (OD T111 D1).
- **Volcar `str(exc)` o `result` en el log para diagnosticar.** Expone prompts, abstracts y salida del modelo.

## Consecuencias

- **Gasto: sin llamadas nuevas.** `budget.py` y `nightly_tokens` no cambian. Antes, un 429 seguido de `ProcessError` llegaba como `LLMError` y el consumidor lo trataba como fallo común. Ahora llega como `LLMRateLimited`, y el consumidor puede cortar la noche sin reintentar (`budget-guard-review` § 6). Revisión `budget-guard-review` aprobada en el ciclo 1 de T111.
- **Límite conocido.** `assistant_error` guarda el último error no nulo. Con herramientas o MCP, una señal de un turno anterior podría reclasificar un fallo posterior. Anotado en el código, en el bucle de `run_agent`. Revisar antes de dar herramientas a un rol.
- **Historia.** Las notas de las noches del 10-29 y 10-30 no se reescriben. El informe matinal sigue clasificando el 10-30 como `circuit_breaker` hasta T112 (`AUTH_FAILED`).
- **Documentos.** `ARCHITECTURE.md` (proveedor LLM) y `CLAUDE.md` (estado actual). `TECHNICAL_DEBT.md` marca como saldado el hueco del 429. ADR 0045 no se edita (publicado).
