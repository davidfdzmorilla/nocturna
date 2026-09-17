# ADR 0008 · El Editor: llamada N:1 y estimación de coste

Fecha: 2026-09-17 · Estado: aceptado

## Contexto

T43 implementa el tercer agente: Editor, orquestador de publicación. A diferencia de Reader y Popularizer (que se llaman por ítem, N llamadas por noche), el Editor recibe **todos** los candidatos del Popularizer de la noche en una **sola conversación**. Eso es N:1: un Editor, una llamada. La entrada es voluminosa (lista de títulos + niveles de curiosidad para 20–40 ítems), la salida de alto valor (decisiones de publicación). El modelo es Opus. Las decisiones arquitectónicas de T43 que T30 no contempló son: estimación de coste lineal, timeout rol-dependiente, validación de reserva presupuestaria.

## Decisión

### 1. Llamada N:1 con entrada acotada

El Editor se llama como máximo una vez por noche y recibe todos los candidatos en un único `prompt`:

```
Tienes una lista de hallazgos candidatos:
- item_id=123, title="...", level_curious="...~100 palabras"
- item_id=456, title="...", level_curious="...~100 palabras"
...
```

No recibe `level_amateur` ni `level_technical` (eso son ~3× más entrada). La entrada para cada candidato es: `item_id`, `title` y `level_curious` solamente.

**Motivo**: reducir tokens de entrada. `level_amateur` + `level_technical` suman ~340 palabras extra por candidato. Con 40 candidatos, son ~13.600 palabras = ~9.500 tokens de entrada pura. El prompt base del Editor es ~500 tokens; 40 candidatos a ~450 tokens/entrada = ~18.000 tokens solo en datos. Omitiendo dos niveles, 40 × ~150 tokens/candidato = ~6.000 tokens, diferencia de ~3.5×. Mantener la entrada baja acelera decisiones, preserva presupuesto y facilita calibración.

### 2. Estimación lineal: `base + N × per_candidato`

El coste estimado es:

```
estimated_tokens = editor_base_tokens + (num_candidates × editor_tokens_per_candidate)
```

Configuración (en `pipeline.toml`):

```toml
[budget]
editor_base_tokens = 4000           # prompt base + instrucciones
editor_tokens_per_candidate = 700   # entrada + salida por candidato
editor_reserve_tokens = 60000       # reserva semanal separada
```

**Motivo**: el modelo Opus es 3.5× más caro que Sonnet. Una sobreestimación barata baja el presupuesto de Reader/Popularizer, afectando throughput de la noche. Una subestimación caro desborda la reserva y fuerza `partial`. La fórmula lineal funciona para agregados (Reader: todos los abstracts tienen tamaño similar; Editor: todos reciben la misma lista con tamaño predecible). Se calibra en T60 contra datos reales de Opus.

### 3. Validador de reserva: restricción cerrada al cargar configuración

`PipelineConfig.__init__` (Pydantic) valida durante la carga:

```python
if (policy.editor_base_tokens + 
    policy.limits.max_items_per_night * policy.budget.editor_tokens_per_candidate
    > policy.budget.editor_reserve_tokens):
    raise ValueError("Editor estimation exceeds reserve")
```

Condición con `max_items_per_night` (40 por defecto):

```
4.000 + (40 × 700) = 4.000 + 28.000 = 32.000 ≤ 60.000 ✓
```

**Falla cerrado**: si una calibración futura de T60 rompe el invariante (ej. suben `max_items_per_night` a 60), la configuración se rechaza al iniciar `nocturna`, de día, cuando hay tiempo de revisar. No se descubre a las 03:00 cuando el presupuesto se agota en plena noche.

**Motivo**: garantía en el composition root (`cli.py`). Nada puede iniciar una noche si la configuración incumple el invariante.

### 4. Timeout rol-dependiente (extensión de ADR 0005 § 10)

Tras T43, `BudgetGuard.timeout_for_call(role)` diferencia entre roles:

```python
def timeout_for_call(self, role: AgentRole | None = None) -> int:
    item_or_editor_timeout = (
        self._policy.editor_timeout_s if role is AgentRole.EDITOR
        else self._policy.item_timeout_s
    )
    return min(item_or_editor_timeout, self.seconds_until_hard_stop())
```

Reader/Popularizer: `item_timeout_s = 180` s (3 min).
Editor: `editor_timeout_s = 300` s (5 min).
Ambos: `min(..., hard_stop)`.

**Motivo**: el Editor procesa múltiples candidatos en una sola conversación; más tiempo es necesario. La invariante de ADR 0005 se preserva: ambos roles están ligados a `hard_stop` con el segundo término del `min()`. A las 04:45 exacto, ningún rol obtiene tiempo; el veto final es inmutable. Sin cambios en `window` ni relajación de garantías.

### 5. El fallo del Editor no toca Findings

Si el Editor retorna JSON inválido, timeout o error:

- Los `Finding` candidatos quedan sin `published_at` ni `confidence`.
- Los `Item` correspondientes quedan en estado `READ` (no se avanzan).
- El `Run` se marca `PARTIAL` o `FAILED` según el motivo.
- Ninguna noche futura reintenta esos `Finding`: `unpublished_for_run` filtra por `run_id`, así que quedan huérfanos de facto.

**Motivo**: no contaminamos el histórico. Un `Finding` que no se publica en su noche se quedó sin oportunidad; publicarlo luego cambiaría su `published_at` ficticiamente. La corrección adecuada es un reintento manual en T44 (futuro) o un `unpublished_for_run` que no filtre por `run_id` (cambio de diseño futuro). Hoy: una denegación del Editor cierra la noche en `partial`.

### 6. Motivos de rechazo del Editor no se persisten

La respuesta del Editor es:

```json
{
  "decisions": [
    {"item_id": 123, "publish": true, "confidence": 0.9, "reason": "Novel observation"},
    {"item_id": 456, "publish": false, "confidence": 0.3, "reason": "Too speculative"}
  ]
}
```

Hoy se persiste: `item_id`, `publish`, `confidence`. El campo `reason` **se descarta** (solo log estructurado, no en `Finding`).

**Motivo**: `findings` es la tabla pública; el `reason` es telemetría de decisión que cabe en logs, no en el esquema. Persistirlo exigiría una columna nueva, una migración Alembic, cambios en `Finding`, y `CLAUDE.md` no lo contempla (solo `title`, `level_curious/amateur/technical`, `confidence`). Si T60 elige "la razón importa para la retro", se abre como decisión en T61 (cambio de esquema). Hoy: se registra en logs estructurados JSON (`decision_reason`) para análisis de calibración.

## Alternativas descartadas

- **Llamada N (múltiples editores, cada uno con un subset)**: divide la responsabilidad editorial, pero complica la lógica de quién publica qué. Una sola llamada es más cara pero coherente.
- **Incluir `level_technical` en la entrada del Editor**: añade ~9.500 tokens. El Editor tal vez lo necesite para hacer mejores decisiones, pero la calibración sin datos reales no lo justifica. Si T60 lo requiere, es un cambio de prompt (nueva versión), no de arquitectura.
- **Presupuestar por debajo de `max_items_per_night`**: "siempre sobra, así que estimamos conservadores" es ilusorio. Funciona hasta que Reader encuentra 26 candidatos y Editor no cabe. La fórmula lineal es honesta: si se agotan candidatos, se agotan tokens estimados.
- **Motivos del Editor en tabla aparte**: complejiza la lectura de `findings` sin agregar valor en fase 1. Los logs estructurados son suficientes.
- **Timeout igual para todos los roles**: Reader y Popularizer procesan un ítem en 3 minutos; Editor podría necesitar más. Diferenciar no viola la invariante de `hard_stop`.
- **Reintento automático si el Editor falla**: cada intento es una llamada N:1 completa. El presupuesto no es suficiente para 2 intentos en la mayoría de noches. El reintento es decisión manual de T44 si la política cambia.

## Consecuencias

- **Presupuesto del Editor es acotado, no abierto**: el validador de reserva permite que T60 calibre con datos reales sin miedo a divergencias.
- **Motivo de rechazo del Editor es log, no dato persistido**: existe en `run.notes` (JSON libre del orquestador); no en `findings`.
- **Candidatos huérfanos tras un fallo del Editor no se republican**: es característica correcta de fase 1; T44 es responsable de decidir si se reintenta.
- **Timeout del Editor es mayor, pero `hard_stop` es inviolable**: 5 minutos son los del Editor; con 10 minutos para `hard_stop` lo retaría, con 1 minuto aún se honra `hard_stop`.
- **Entrada rol-dependiente de `BudgetGuard.timeout_for_call`** permite que T44 pase el rol y sea consciente de cuánto tiempo tiene.
- **Diferencia de estimación (4.000 + 700×N) frente a datos reales de Opus es detectada en T60**: una llamada N:1 que gasta ~30.000 tokens reales frente a 32.000 estimados para 40 candidatos es buena calibración. Peor caso: 44.000 reales → ajustar estimador para próxima noche.
