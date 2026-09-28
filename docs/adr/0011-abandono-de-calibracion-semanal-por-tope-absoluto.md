# ADR 0011 · Se abandona la calibración por % semanal; el control es el tope absoluto por noche

Fecha: 2026-09-28 · Estado: aceptado · **Rectifica CLAUDE.md** (en lo relativo a "Presupuesto: 30% de la semana" como magnitud medible) · **Cierra T60**

## Contexto

`CLAUDE.md` fija el presupuesto del pipeline en el 30 % del límite semanal de la suscripción, y T60 planteó catorce noches leyendo Settings > Usage antes y después de cada noche para convertir tokens del pipeline en puntos porcentuales (constante `k`, columnas `usage_A`, `usage_B`, `Δ%` de `docs/CALIBRACION.md`) y ajustar `budget.nightly_tokens` con ese dato.

El autor usa la misma suscripción en varios proyectos. El porcentaje de Settings > Usage mezcla todos esos consumos y no hay forma de aislar lo que gasta Nocturna. La medida en la que se apoyaba T60 no existe.

Lo que sí se mide con exactitud es el gasto del propio pipeline: `AgentCall` registra los tokens de cada llamada y `BudgetGuard` aplica `budget.nightly_tokens` sobre esa suma (incluido el gasto lateral del CLI, contabilizado según ADR 0007).

## Decisión

1. **El control de gasto es el tope absoluto por noche**, `budget.nightly_tokens`, que se mantiene en **300.000**. No cambia ningún mecanismo de `budget.py`.
2. **El 30 % semanal pasa a ser orientativo**: expresa la intención, no una magnitud que el pipeline mida ni pueda verificar.
3. **Ajuste manual**: si el autor nota que se queda sin límite semanal en sus otros proyectos, baja `nightly_tokens` a mano. No hay fórmula.
4. **T60 se cierra** con cuatro noches automáticas (2026-09-25 a 2026-09-28) como verificación de estabilidad, en lugar de catorce noches de calibración.

Evidencia de esas cuatro noches (datos de `runs` y `agent_calls`):

| Noche | Status | Nuevos | Leídos | Candidatos | Publicados | Tokens | % del tope |
|---|---|---|---|---|---|---|---|
| 2026-09-25 | partial (fallo de ingesta por la vía `api`) | 0 | 40 | 18 | 13 | 252.328 | 84,1 % |
| 2026-09-26 | completed | 31 | 40 | 17 | 12 | 241.842 | 80,6 % |
| 2026-09-27 | completed | 0 | 40 | 18 | 13 | 248.068 | 82,7 % |
| 2026-09-28 | completed | 0 | 30 | 18 | 12 | 226.760 | 75,6 % |

Ninguna noche superó el tope ni terminó por `hard_stop`.

## Consecuencias

- Se retiran de `docs/CALIBRACION.md` el paso de Settings > Usage, las columnas `usage_A`, `usage_B`, `Δ%` y la constante `k`. La tabla de noches se conserva como registro histórico.
- `.claude/commands/calibrate.md` queda marcado como retirado.
- Las decisiones abiertas que dependían de medir el % semanal quedan resueltas o sin objeto (ver `OPEN_DECISIONS.md`).
- `budget.weekly_reset_weekday`, `weekly_reset_hour` y `reset_day_multiplier` siguen en configuración con su valor provisional e inerte; no hay dato con el que ajustarlos.
- Riesgo aceptado: el pipeline puede consumir más del 30 % de la semana sin que nadie lo detecte. La protección real es el tope nocturno más la observación del autor.

## Alternativas descartadas

- **Seguir registrando Settings > Usage**: mediría ruido de otros proyectos; columnas sin decisión que alimentar.
- **Dedicar una semana a no usar la suscripción fuera de Nocturna para aislar la medida**: no es viable para el autor.
