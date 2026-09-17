# ADR 0006 · Frontera del proveedor LLM y contabilidad de tokens

Fecha: 2026-09-16 · Estado: aceptado

## Contexto

T40 implementa `AgentSDKProvider`, la única vía de llamada a un agente durante el pipeline. La decisión arquitectónica debe responder dos preguntas de alcance incompleto:

1. **¿Qué responsabilidades tiene el proveedor?** Si no persiste, ¿quién garantiza que el gasto se registra aunque el proceso muera?
2. **¿Cómo se contabilizan tokens en la ruta de error**, cuando no hay `ResultMessage`?

Ambas afectan a T41–T44 (agentes y orquestador).

## Decisión

### 1. El proveedor no conoce `BudgetGuard`, `config` ni `db`

`LLMProvider.run_agent` recibe un `AgentRequest` con `role`, `model`, `prompt`, `max_turns`, `timeout_s` e `item_id`. **Todo lo que necesita, nada más.**

- No importa `PipelineConfig` (la configuración viene en el request).
- No importa `BudgetGuard` (la autorización es responsabilidad del llamador).
- No llama a `AgentCallRepository.add()` (la persistencia es responsabilidad del llamador).

El proveedor es un adaptador puro entre `AgentRequest` y el SDK.

**Motivo**: desacoplamiento. Si T41 decide persistir antes de llamar, y T42 decide persistir después, ambos pueden hacerlo. Si el proveedor persistiera, ambas estrategias pasarían por ese único punto, perdiendo flexibilidad. Además, permite remplazar el proveedor (pasar a `ApiKeyProvider`) sin tocar la orquestación de persistencia.

### 2. La secuencia de gasto vive en el caso de uso, no en el proveedor

```python
# Patrón que T41, T42 y T43 heredan de T40 (test_sdk_smoke.py):

with unit_of_work(session_factory) as session:
    guard = BudgetGuard(...)
    guard.authorize(role, estimated_tokens)
    timeout_s = guard.timeout_for_call()

# Sin transacción abierta: ninguna conexión PostgreSQL esperando al LLM
result = await provider.run_agent(request)

# Transacción nueva para registrar
with unit_of_work(session_factory) as session:
    guard = BudgetGuard(...)
    call = AgentCall(...)
    guard.record_call(call, run)
```

Las transacciones se abren y cierran alrededor del LLM, nunca durante la espera. `BudgetGuard` cierra su sesión antes de que `run_agent` bloquee al LLM.

**Motivo**: ninguna conexión de la piscina PostgreSQL permanece abierta mientras se espera respuesta (hasta `item_timeout_s` segundos). Los recursos se liberan antes de la espera. Si la llamada falla, `run_agent` lanza la excepción; el caso de uso la atrapa y registra el gasto en su propia `unit_of_work`.

### 3. `tokens_in` incluye tokens de caché (creación y lectura)

La sentencia `usage` que trae `ResultMessage` contiene en `input_tokens`:

- Input normal (prompt + contexto previo).
- `cache_creation_input_tokens` (tokens que abrieron una nueva ranura de caché).
- `cache_read_input_tokens` (tokens leídos desde caché, reutilizados sin recompilación).

Todos ellos cuentan para `tokens_in`. Esto **cambia el significado del presupuesto** respecto a ADR 0005.

**Motivo**: la suscripción Claude Max cobra por ellos. Los tokens de caché se pagan a tarifa reducida (10% de la normal en el primer turno, 25% después), pero se pagan. Si se excluyeran de la contabilidad, el presupuesto sería optimista y facturaría más que lo presupuestado. Además, permitir caché sin contabilizarla invita a un bucle `while True: query()` que usa caché al máximo sin que el guard lo vea.

Enlace a ADR 0005 § 3: ahora que sabemos que caché cuenta, la lectura conservadora "cualquier token que consumimos" es correcta.

### 4. Sin `ResultMessage`: 0 tokens contabilizados (fuga documentada)

Si `run_agent` lanza `CancelledError` por timeout de socket (no de SDK), no hay `ResultMessage` y el error no trae `usage`. El contrato dice: los atributos `tokens_in`/`tokens_out` viajan en la excepción. Si no están (segundo `await` sobre cancelación externa, timeout de red), se contabilizan como cero.

**Cifra de riesgo**: una noche de timeouts (red lenta, arXiv caído) puede gastar **600.000–800.000 tokens de verdad con la base de datos marcando 0**. Acotado por `CALL_LIMIT_REACHED` a 80 llamadas, pero sin control fino.

**Mitigaciones propuestas** (para T41 / T44):

- **(a) Contador de fallos consecutivos**: si fallan 5 llamadas seguidas, cierra el `Run` como `partial` sin rellamadas. Reduce el techo a ~50.000 tokens.
- **(b) Contabilidad pesimista**: si no hay `usage`, estimar desde el prompt (contar caracteres, extraer una cifra conocida). Impreciso pero mejor que cero.
- **(c) Bajar tolerancia**: reducir `item_timeout_s` y `max_calls_per_item` tras T60. Afecta a todo, no solo a timeouts.

Decisión de T41 / T44: **no anticipar**. Documentado, vigilado en T60.

### 5. Terminales distintas (`terminal_reason`) definen el status del `Run` y del `AgentCall`

Si `AgentCall.status != ok`:

- `AgentCall.status = invalid_output` si el JSON no valida al primer reintento.
- `AgentCall.status = timeout` si la llamada expira por `item_timeout_s` o `hard_stop`.
- `AgentCall.status = error` si `run_agent` lanza cualquier otra `LLMError`.

El status del `Run` lo define T44: si hay algún `AgentCall` con status ≠ `ok`, la noche es `partial` (si presupuesto restante) o `failed` (si error no controlado).

**Motivo**: distinguir intentos fallidos de intentos exitosos permite el reintento y contabilización correcta. Ver ADR 0005 § 9: contar **todos** los intentos, no solo los exitosos.

### 6. Sin `output_format`: salida libre con validación Pydantic + reintento manual

`AgentSDKProvider` no usa `output_format` del SDK. La respuesta es texto libre; el caso de uso valida con Pydantic y reintenta una vez si falla.

**Motivo**: `output_format` hace que el SDK reintente sin pasar por `BudgetGuard.authorize` — el gasto del reintento no se contabiliza. Hereda la mitad de los tokens de la primera llamada (el modelo requiere menos confirmación) pero sin registrarlo. Con reintento manual, ambos intentos pasan por `authorize → run_agent → record_call`, y el presupuesto ve el gasto real. ADR 0005 § 3 de nuevo: nada puede eludir el guard.

Extensión futura: si una versión más nueva del SDK expone el gasto del reintento en `output_format`, cambiar es aditivo — la llamada sigue siendo controlada.

### 7. `setting_sources=[]`, `tools=[]`, `skills=[]` son explícitos y no negociables

```python
options = ClaudeAgentOptions(
    model=...,
    system_prompt=...,
    setting_sources=[],  # NO cargar .claude/ del proyecto
    tools=[],            # NO heredar herramientas del CLI
    skills=[],           # NO heredar skills de .claude/
    mcp_servers={"arxiv": ...},  # Solo lo que pasamos
    allowed_tools=["mcp__arxiv__get_abstract"],  # Solo lo que permitimos
)
```

Ambas vagas (`setting_sources=None`, `tools=None`) permiten que el SDK cargue contexto de proyecto y herramientas del CLI por defecto.

**Cifras de impacto**:

- `setting_sources=None`: cada `query()` carga `CLAUDE.md`, `.claude/settings.json` y agentes de desarrollo (~3.500–5.000 tokens) × ~51 llamadas/noche = **180.000–255.000 tokens/noche (60–85% del presupuesto), 5,5–7,7 millones/mes**. Una sola noche hace burn.
- `tools=None`: el CLI declara su toolset completo (~240.000–800.000 tokens/noche de preámbulo, **1–3 noches de presupuesto** quemadas en overhead).

Estas decisiones están **congeladas por tests**: `test_agent_sdk_options.py` falla si alguno de estos valores cambia.

**Motivo**: son el ajuste de mayor impacto económico de T40. Si se relajan sin saber, T41–T44 funcionan pero queman 2–4 noches de presupuesto cada noche sin razón visible. Imposible de depurar a posteriori sin revisar el SDK output.

### 8. Timeout de LLM acotado por `hard_stop`, no `item_timeout_s` solo

`BudgetGuard.timeout_for_call()` devuelve `min(item_timeout_s, segundos_hasta_hard_stop)`.

Una llamada individual nunca sobrevive a `hard_stop`. Si `hard_stop` es 04:45 y son las 04:44, `timeout_for_call()` devuelve 60 segundos máximo. Si son las 04:45:10, devuelve 0 → no hay tiempo para lanzar.

**Motivo**: ver ADR 0005 § 10. T44 usa esto para no iniciar llamadas que van a expirar seguro.

## Alternativas descartadas

- **El proveedor persiste directamente**: rompe la separación de responsabilidades. Ambos casos de uso (T41, T42) o todos (T41–T43) tendrían que pasar por ese punto, sin flexibilidad.
- **Memoizar `tokens_in`/`tokens_out` en `AgentCall.message`**: la fuente de verdad es la excepción y `ResultMessage`, no una copia. El `AgentCall` es el registro; no replicar datos que la excepción ya transporta.
- **Excluir tokens de caché de la contabilidad**: optimista. Facturaría más de lo presupuestado.
- **Asumir que `CancelledError` siempre trae atributos**: un segundo `await` sobre cancelación externa o un timeout de red sin `ResultMessage` no los tiene. Crash silencioso.
- **`output_format` nativo del SDK**: reintento sin autorización = gasto invisible.
- **Dejar `setting_sources=None`, `tools=None`**: 1–4 noches de presupuesto se queman cada noche sin razón.
- **Timeout solo por `item_timeout_s`**: una llamada iniciada a las 04:44:50 se extiende 50 segundos para un timeout de 30, cruzando `hard_stop`.

## Consecuencias

- **T41–T44 heredan la misma pauta**: `authorize → run_agent → record_call` en tres `unit_of_work` distintos.
- **El gasto que se contabiliza es real**: incluye caché, cuenta todos los intentos, ninguno queda invisible.
- **El riesgo residual (fuga sin `ResultMessage`) está mapeado** y acotado por `CALL_LIMIT_REACHED`. T60 lo calibra.
- **El presupuesto económico es fiable**: los 300.000 tokens/noche son 300.000, no "más según cuántos tengas de caché". No hay sorpresas en Settings > Usage a las 8 de la mañana.
- **La frontera del proveedor es impermeable**: reemplazar a `ApiKeyProvider` no toca ni un test de orquestación; es un cambio de configuración + autenticación.
