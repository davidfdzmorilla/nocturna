# ADR 0025 · Reserva anidada del redactor

Fecha: 2026-10-08 · Estado: aceptado · Tarea: T75 · Relacionados: ADR 0005 (control de gasto), ADR 0008 (Editor), ADR 0012 §4 y §12, ADR 0020 §4

## Contexto

La fase 2 añade un rol que redacta los hallazgos de tensión (T76) y, más adelante, quizá otros textos de la noche (T84, T85). ADR 0012 pedía una reserva fija para ese rol, análoga a la del Editor, sin subir `nightly_tokens` (300.000), con un tamaño provisional de 20–40k. T75 esperaba siete noches con `reader-v3` para fijar los valores con datos reales. Medidas hasta el 2026-10-08: noches de 137.854 a 246.269 tokens; `reader-v3` de 4.865 a 6.142 de media por llamada (máximo 7.437); Popularizer 5.290 de media (máximo 11.492); Editor 12.636 de media (máximo 19.944); 3 tensiones con σ ≥ 3 en 9 noches, es decir, 0–1 por noche.

## Decisión

1. **Rol `writer`** en `AgentRole` (y en el CHECK de `agent_calls`, migración `c9e4b2a7d135`). Un único rol y una única reserva para todo lo que redacte la noche; cada tarea que lo use revisa tope y reserva.
2. **Reparto anidado**: el Editor ve el presupuesto completo B; el redactor ve B − E; Reader y Popularizer ven B − E − W, desde la primera llamada de la noche. Una sola función (`available_tokens_for`) implementa el reparto, y la usan `BudgetGuard` y `run-night --dry-run`. El gasto acumulado se lee siempre de `agent_calls`, así que ningún rol puede consumir la reserva de un rol posterior. Las reservas no escalan con el multiplicador del reinicio semanal.
3. **Valores** (`pipeline.toml`, sin valores por defecto en código): `writer_reserve_tokens = 24.000`, `writer_estimated_tokens = 12.000` (≈ 1,9× la media esperada, criterio de `reader-v3`), `max_writer_calls_per_night = 2` (una tensión con reintento o dos sin él). La reserva sale de la porción de Reader y Popularizer (240.000 → 216.000); la del Editor sigue en 60.000.
4. **Tope propio del redactor**: cuenta intentos de cualquier estado, como el del Editor, y al alcanzarlo se deniega con `CALL_LIMIT_REACHED`. Un rol sin tope explícito hace fallar la comprobación. Para T76: una denegación del redactor (tope o presupuesto) no cierra la noche con `terminal_status_for`, salvo `OUTSIDE_WINDOW`; la noche sigue hacia el Editor.
5. **Validadores al cargar** (fallan cerrado): `editor_reserve + writer_reserve < nightly_tokens`; `max_writer_calls_per_night × writer_estimated_tokens ≤ writer_reserve`; y el peor caso del Editor cuenta también los candidatos que puede producir el redactor: `editor_base + (max_items_per_night + max_candidates_per_night + max_writer_calls_per_night) × per_candidate ≤ editor_reserve` (33.950 ≤ 60.000).
6. **Activa desde el merge**, aunque el redactor no se llame hasta T76: se mide primero el efecto del pool más pequeño, por separado.

## Consecuencias

- En las noches pesadas el Popularizer se corta antes: con los datos medidos, 2 de 8 noches habrían perdido ≈ 1–4 candidatos `paper_explained`. El presupuesto nunca se rebasa: la degradación es publicar menos.
- Hasta T76, unos 24.000 tokens por noche quedan sin usar.
- `run-item` y la relectura de T82 también ven la porción reducida para el Reader.
- `night_report.sql` calcula el pool como B − E − W.
- Volver atrás es cambiar tres valores de `pipeline.toml`; la migración no baja si ya hay llamadas del redactor.

## Alternativas descartadas

- **Bajar la reserva del Editor a 50.000** para no tocar el pool: quita holgura al reintento del Editor en noches grandes y cambia dos reservas a la vez.
- **Tope de 3 llamadas** (36.000): afectaría a más noches sin datos que lo justifiquen.
- **Dejar la reserva a 0 hasta T76**: mezclaría el efecto del pool más pequeño con el del redactor.
