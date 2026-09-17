# ADR 0007 · Contabilidad de tokens con modelos internos del CLI

Fecha: 2026-09-17 · Estado: aceptado · Supersede: ADR 0006 § 3

## Contexto

ADR 0006 fijó la política de conteo el 2026-09-16, antes de cualquier llamada real. Afirmó: «`tokens_in` incluye tokens de caché (creación y lectura)» y el gasto se calcula como `usage.input_tokens + usage.output_tokens` completo.

El 2026-09-17, el test de humo manual ejecutó una sola llamada real al Reader con modelo Sonnet. Los datos refutaron la premisa:

```
ResultMessage.usage = {
  'input_tokens': 523, 'output_tokens': 6,
  'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0
}
model_usage = {
  'claude-haiku-4-5-20251001': {'inputTokens': 946, 'outputTokens': 17, ...},
  'claude-sonnet-5': {'inputTokens': 523, 'outputTokens': 6, ...}
}
total_cost_usd = 0.0021200000000000004
```

Gasto reportado por `usage`: 529 tokens (Sonnet solo).
Gasto real según facturación: 1.475 tokens (946 Haiku + 529 Sonnet).
**Subregistro: 35,9% de los tokens reales; factor 2,788×.**

Confirmación por aritmética: `0.001014 (Haiku) + 0.001106 (Sonnet) = 0.00212 = total_cost_usd`, sin residuo.

## Decisión

El gasto contabilizado de una llamada es el **máximo componente a componente** entre:
- `usage.input_tokens` + `usage.output_tokens` (incluye caché del modelo pedido)
- Suma de `model_usage[*].inputTokens + model_usage[*].outputTokens` (todos los modelos que el CLI usó)

```python
# Pseudocódigo
tokens_in_recorded = max(usage.input_tokens, sum(model_usage[*].inputTokens))
tokens_out_recorded = max(usage.output_tokens, sum(model_usage[*].outputTokens))
tokens_total = tokens_in_recorded + tokens_out_recorded
```

**Por qué máximo y no suma**: ambas fuentes reportan el mismo evento (la llamada), solapan completamente. Sumarlas duplicaría la contabilidad. La política del proyecto es sobreestimar ante ambigüedad (ver CLAUDE.md § "Control de gasto"), así que máximo es la opción conservadora: nunca resta.

**Por qué no preferencia estricta por `model_usage`**: un `model_usage` parcial (e.g., si el SDK retrasara la facturación de un modelo) podría ser menor que `usage`, y elegirlo reduciría el registro. El máximo protege contra eso.

**Aplicación**: en todos los sitios que leen tokens — `AgentCall` persistido, excepción `LLMError`/`LLMTimeout`, `CancelledError` — se aplica este máximo. T40 implementó la extracción en nueve caminos; T41–T44 heredan el patrón.

## Alternativas descartadas

- **Solo `usage`**: optimista, subregistra 35,9% en fase 1 con gasto de CLI.
- **Solo suma de `model_usage`**: asume que el SDK siempre reporta todos los modelos; riesgo de subestimar si el SDK cambia.
- **Suma de ambas fuentes**: duplicaría la contabilidad; un mismo token se contaría dos veces.

## Consecuencias

**Política de conteo ahora correcta**: los 300.000 tokens/noche presupuestados son 300.000 reales, no "optimista según caché y modelo interno".

**Lo que sigue sin contabilizarse** (fuga documentada en TECHNICAL_DEBT.md):
- Llamadas que mueren antes de `ResultMessage` (timeout de red, cancelación externa, kill del proceso): no hay `usage` ni `model_usage`. Cifra de riesgo: ~600.000–800.000 tokens/noche en escenario de arXiv caído, acotada por `CALL_LIMIT_REACHED` (80 llamadas máximo).
- Gasto de sesión MCP (Haiku para control del CLI): ~946 tokens/sesión fijos, no configurable desde `pipeline.toml`. Con 82 sesiones/noche en fase 1 (40 ítems × 2 turnos + Editor) = ~77.572 tokens/noche. **Debe calibrarse en T60 contra Settings > Usage.**

**Qué parte de ADR 0006 sigue vigente**:
- Frontera del proveedor (§ 1–2): `LLMProvider` no conoce `BudgetGuard`, `config`, `db`. Sigue siendo correcto.
- Contrato de errores (§ 4–5): `LLMError`/`LLMTimeout` transportan tokens, `CancelledError` puede no tenerlos, fuga sin `ResultMessage`. Sigue siendo correcto. Este ADR solo cambia **cómo se extrae y agregan** los tokens de esas fuentes.
- `setting_sources=[]`, `tools=[]`, `skills=[]` (§ 7): congelado por tests, impacto de 180.000–255.000 tokens/noche. Sigue siendo no negociable.
- Timeout de LLM acotado por `hard_stop` (§ 8): sigue siendo correcto.

## Validación

Identidad de validación para futuras sesiones:
```
total_cost_usd == sum(costUSD en model_usage)
```

Si `total_cost_usd` diverge de la suma de `costUSD`, hay un tercer sumidero (e.g., `webSearchRequests` facturado por petición). T60 debe verificar diariamente durante calibración.
