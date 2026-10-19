# ADR 0036 · Estimación del Editor con `editor-v4`

Fecha: 2026-10-19 · Estado: aceptado · Tarea: T98 · Relacionados: ADR 0008 §2, ADR 0011, ADR 0025, ADR 0030

## Contexto

Cada intento del Editor se autoriza con la estimación `editor_base_tokens + N × editor_tokens_per_candidate`, donde N es el número de candidatos de la noche. El reintento por JSON inválido usa la misma estimación, y el validador de configuración la aplica al peor caso (N = `max_items_per_night + max_candidates_per_night + max_writer_calls_per_night` = 37) para comprobar que cabe en `editor_reserve_tokens`.

Los valores vigentes, 2.500 + 850 × N (cfg-2026-09-18), salieron de dos observaciones de `editor-v1`. Desde entonces el prompt creció: `editor-v2` (T89), `editor-v3` (T76) y `editor-v4` (T85.e), que añaden tipos y líneas `data`.

Llamadas reales de `editor-v3` y `editor-v4` (consulta de solo lectura, 2026-10-19; N tomado de `runs.notes`, `candidates=N`):

| Noche | Prompt | N | Real | Estimado (2.500 + 850 N) |
|---|---|---|---|---|
| 2026-10-09 | v3 | 18 | 17.956 | 17.800 |
| 2026-10-10 | v3 | 10 | 10.997 | 11.000 |
| 2026-10-14 | v3 | 15 | 15.636 | 15.250 |
| 2026-10-15 | v4 | 10 | 12.218 | 11.000 |
| 2026-10-16 | v4 | 11 | 11.988 | 11.850 |
| 2026-10-17 | v4 (JSON inválido) | 10 | 11.277 | 11.000 |
| 2026-10-17 | v4 | 10 | 11.427 | 11.000 |
| 2026-10-18 | v4 | 17 | 17.953 | 16.950 |
| 2026-10-19 | v4 | 10 | 12.502 | 11.000 |

En 8 de 9 llamadas el gasto real superó la estimación, hasta en 1.502 tokens (13,7 %). La reserva de 60.000 absorbe la diferencia con mucho margen: el máximo real de una noche fue 22.704 tokens, con los dos intentos del 2026-10-17. Pero la estimación ya no es una cota honesta del gasto, y el validador del peor caso depende de ella.

La regresión por mínimos cuadrados de `editor-v4` da `a = 2.952`, `b = 877`, r² = 0,95, con N entre 10 y 17.

## Decisión

Decisión del autor (T98): D1–D8 con las recomendaciones del plan. Modifica las cifras de ADR 0008 §2 y de cfg-2026-09-18, que no se editan.

1. **Valores** (cfg-2026-10-19): `editor_base_tokens = 4.000` y `editor_tokens_per_candidate = 900`.
2. **Método.** Recta de mínimos cuadrados de la versión vigente del Editor, desplazada hacia arriba por el mayor residuo positivo de las versiones que pueden ejecutarse: v3 vuelve si se apaga `writer_enabled`. Aquí el residuo es +777,5. Se redondea al alza: la base a múltiplos de 500 y la pendiente a múltiplos de 50.
   - Si r² < 0,5 o el rango de N es menor de 5, la pendiente no baja y solo se sube la base.
   - Cubre las 9 llamadas. La más justa es la del 2026-10-19: 12.502 frente a 13.000.
   - Un solo par de constantes vale para todas las versiones del Editor.
3. **Peor caso validado.** 4.000 + 37 × 900 = 37.300 ≤ 60.000 para un intento. El reintento sigue autorizándose con el gasto real del primero ya sumado, y el validador no cubre dos intentos de peor caso (D5 a).
4. **Sin cambios** en la reserva de 60.000, en `budget.py`, en `estimate_editor_tokens`, en el validador ni en `max_editor_calls_per_night = 2`.
5. **Vigilancia.**
   - `night_report.sql` Q10 compara cada llamada del Editor con su estimación.
   - Cada versión nueva del prompt del Editor se revisa con ella y con este método.
   - Un test carga las llamadas observadas (`tests/fixtures/t98/editor_calls.json`) y falla si las constantes quedan por debajo de alguna.

## Consecuencias

- La estimación sube 1.500 + 50 × N tokens por intento. Con N = 10, 13.000 en lugar de 11.000.
- El reparto anidado no cambia (ADR 0025): Reader y Popularizer, 216.000; redactor, 240.000; Editor, 300.000.
- El Editor queda denegado solo si las fases anteriores dejan menos de la estimación libre. Con las cifras actuales no ocurre.
- El peor caso validado pasa de 33.950 a 37.300. Dos intentos de peor caso (74.600) siguen sin caber en la reserva; es la asimetría que ya documentaba la nota T43/T60, ahora con el dato real.
- **Lo que la fórmula no distingue:**
  - el coste por tipo de candidato (los de medida y tensión llevan una línea `data`);
  - el gasto lateral del CLI, que va dentro de la base y cambia entre versiones.

  Quedan como decisiones abiertas.

## Alternativas descartadas

- **Percentil de los totales:** ignora N y rompe el validador por N.
- **Máximo observado más un margen, como constante fija:** contradice la fórmula lineal de ADR 0008.
- **Subir solo la base con la pendiente de 850:** cubre lo observado, pero queda por debajo de la pendiente medida (877) en el peor caso extrapolado.
- **Bajar la reserva a 40.000:** es otra decisión (OD T02); obliga a rehacer las cifras de ADR 0025, `CLAUDE.md` y `night_report.sql`.
- **Exigir que quepan dos intentos de peor caso:** con 4.000 + 37 × 900 obliga a subir la reserva a 74.600 o más.
