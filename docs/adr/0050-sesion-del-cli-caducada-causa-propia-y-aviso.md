# ADR 0050 · Sesión del CLI caducada: causa propia, corte de la noche y aviso

Fecha: 2026-11-09 · Estado: aceptado · Tarea: T112 · Relacionados: ADR 0011, 0045, 0049

## Contexto

ADR 0049 registró que la noche del 2026-10-30 no fue un límite de tasa sino una autenticación caducada: el `AssistantMessage` llegó con `error="authentication_failed"` y sin `api_error_status`. El proveedor lo clasificaba como `LLMError` genérico. El consumidor lo trataba como un fallo común: el Reader hizo cinco llamadas hasta el cortacircuitos, el Popularizer hizo una más, y el informe matinal la clasificó como `circuit_breaker`. Cada llamada de esa noche falló igual, porque la sesión OAuth del CLI es global y no por ítem. Reintentar o seguir con otro rol no servía para nada.

Un fallo de sesión no se arregla esperando a la noche siguiente, como un límite semanal. Se arregla con `claude /login`, y el informe matinal tiene que decirlo.

## Decisión

1. **Clase propia.** `LLMAuthenticationFailed(LLMError)` en `domain/errors.py`. `_llm_error_class` la devuelve cuando `AssistantMessage.error == "authentication_failed"`. Si llega a la vez una señal de 429, gana la autenticación: un fallo de sesión impide cualquier llamada, también la que tendría límite de tasa.
2. **Clasificación en todas las ramas de error de `run_agent`**, como ADR 0049 para el 429: final del bucle, `ResultMessage` con error, `ClaudeSDKError` y `ProcessError`, `Exception`, `last_result is None`, `TimeoutError` ajeno al deadline del llamador y `terminal_reason` no completado. En esta última rama, `assistant_error == "rate_limit"` pasa a dar `LLMRateLimited` (antes `LLMError`), para ser coherente con «todas las ramas».
   `LLMTimeout` por deadline real y `CancelledError` no cambian. El éxito no cambia. `llm.call_failed` registra `error_class="LLMAuthenticationFailed"`.
3. **Runner sin reintento.** `AttemptOutcome.AUTH_FAILED` es terminal. La llamada queda en `AgentCall` con su `error` y sus tokens.
4. **Casos de uso.** `AUTH_FAILED` en `ReadOutcome`, `PopularizeOutcome`, `EditOutcome` y `WriteOutcome` (lo comparten los tres redactores). El ítem queda en `NEW` o `READ`, según la fase, y ningún `Finding` cambia.
5. **Corte de toda la noche.** `run_night.py` cierra la noche `PARTIAL` con `end=auth_failed_<rol>` (`reader`, `popularizer`, `writer` o `editor`) y no ejecuta las fases que siguen: hallazgos de medida, redactor y Editor. El Popularizer no corre sobre las lecturas previas de la noche (D2 de T112). Una sesión caducada es global: cualquier llamada posterior fallaría igual.
6. **Prioridad del motivo (D1 de T112).** `auth_failed_<rol>` sustituye a un `PARTIAL` previo en `notes`, salvo `KILLED`. Un fallo de ingesta anterior se mantiene en el segmento `ingest=error:`. Un `KILLED` previo no se sustituye.
7. **Causa del informe.** `NightCause.AUTH_FAILED` (prefijo `auth_failed_`) se comprueba primero en `classify_night`. El texto del aviso es «sesión de Claude caducada (<rol>) · renueva la sesión: claude /login», en texto plano (D5). Si hay causas secundarias, la línea se trunca por el final con «…», pero «claude /login» queda visible.
8. **Historia intacta.** Las notas de las noches del 10-29 y del 10-30 no se reescriben. El 10-30 sigue clasificado como `circuit_breaker` en el informe, porque sus notas se escribieron antes de este cambio. ADR 0045 y ADR 0049 no se editan.

## Alternativas descartadas

- **`api_error_status == 401` como señal.** No se ha observado en ninguna transcripción: el 10-30 llegó sin `api_error_status`. Depender de un campo que no aparece en el caso real no detecta nada.
- **Estado nuevo en `AgentCall`** (por ejemplo `auth_failed`). Exigiría migración para un dato que el informe ya puede leer de `notes` y de la causa. Sin beneficio en el diagnóstico.
- **Copiar el tratamiento de `RATE_LIMITED`** (cortar solo la fase). Un límite de tasa afecta a una fase concreta; una sesión caducada afecta a todas las llamadas. Cortar solo la fase dejaría correr las demás con gasto inútil, que es exactamente lo que este ADR evita.
- **Heurística sobre el texto del error.** Ya descartada en ADR 0045 y 0049: frágil entre versiones del CLI.

## Consecuencias

- **Gasto.** Una sesión caducada pasa de 6 llamadas (cinco del Reader hasta el cortacircuitos y una del Popularizer) a 1. `budget.py` y `pipeline.toml` no cambian. Sin migración ni dependencias nuevas. Revisión `budget-guard-review` aprobada en el ciclo 1 de T112.
- **Límite conocido.** La detección usa `assistant_error`, el último error no nulo (límite ya anotado en ADR 0049). Con herramientas o servidores MCP, una señal de un turno anterior podría reclasificar un fallo posterior. Revisar antes de dar herramientas a un rol.
- **Diseño aceptado.** Si la sesión caduca y la llamada se queda colgada hasta el deadline, sale como `TIMEOUT` y no como `AUTH_FAILED`. Cuesta como mucho un ítem más.
- **Pendiente.** `RATE_LIMITED` mantiene su corte incompleto: deja correr el Popularizer y su motivo puede quedar oculto por un `PARTIAL` previo. Queda como OD de T112 (D6), sin cambio de código.
- **Operación.** La causa `auth_failed_<rol>` se resuelve con `claude /login` y no con relanzar la noche (`docs/CALIBRACION.md`, Paso 5).
- **Documentos.** `ARCHITECTURE.md` (proveedor LLM y desenlaces de la noche), `CALIBRACION.md` (Paso 5) y `CLAUDE.md` (estado actual).
