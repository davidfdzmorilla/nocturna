# ADR 0015 · Cálculo determinista de la tensión frente al catálogo

Fecha: 2026-09-30 · Estado: aceptado · **Resuelve** las decisiones abiertas T73 de `OPEN_DECISIONS.md` (fórmula, umbral, varias soluciones del mismo paper, qué `Reading` se cruza) · Tarea: T73

## Contexto

ADR 0012 fija que la discrepancia entre la medida de un paper y las medidas previas del NASA Exoplanet Archive la calcula Python; ADR 0014 deja las medidas del paper en `Reading.measurements`, con `usable_for_tension` como contrato. T73 tenía que decidir cómo se calcula σ y cuándo un resultado es candidato. El caso de referencia es V1298 Tau (paper 2609.30038): frente a la solución por defecto del archivo (Livingston et al. 2026, TTV) las masas del paper están a 3,4–3,9σ (b) y 2,7–3,4σ (e); frente a Suárez Mascareño et al. 2022 (RV), a 0,04–0,53σ (b). Las dos previas discrepan entre sí (3,14σ).

## Decisión

1. **Fórmula**: σ = |x_p − x_i| / √(e_p² + e_i²) en unidad común terrestre (1 M_J = 317,83 M⊕, 1 R_J = 11,209 R⊕, periodo en días). De cada lado se usa el error que mira hacia el otro valor (`err_minus` del paper y `err_plus` de la previa si el paper está por encima; al revés si está por debajo). Valores iguales → σ = 0.
2. **Referencia para decidir candidato**: la solución que el archivo marca por defecto (`is_default`), identificada por igualdad de valor, exactamente una en el grupo. Se calculan y guardan los σ frente a **todas** las previas utilizables, para que la redacción pueda contextualizar (p. ej. "en tensión con Livingston 2026, compatible con Suárez Mascareño 2022").
3. **Umbral**: 3σ. Se aplica fuera del cálculo (`TensionResult.is_candidate(umbral)`) y su valor irá a `pipeline.toml` al cablearlo (T74/T76), sin valor por defecto en código.
4. **Varias soluciones del mismo paper** para el mismo planeta y parámetro: **todas** deben superar el umbral frente a la referencia (σ mínimo). Un `TensionResult` por (ítem, planeta, parámetro) con todas las comparaciones. Con V1298 Tau: b es candidato (mín. 3,40σ), e no (mín. 2,69σ).
5. **Qué se cruza**: todos los `Reading` con medidas utilizables, sin filtro por `interest_score` (el cruce no gasta tokens y la puntuación mide interés divulgativo). Consecuencia para T72: un ítem con puntuación < 4 queda `DISCARDED` aunque tenga tensión.
6. **Sin solución por defecto** entre las previas (es la del propio paper o no tiene ese parámetro): hay resultado con los σ, pero no es candidato.
7. **Previa utilizable**: sin cota y con los dos errores > 0 (error 0 = dato inválido). **Solución propia**: se excluye por `arxiv_id` igual a `Item.external_id` (sin versión); si el adaptador no lo reconoce, el paper se compara consigo mismo y sale σ ≈ 0, fallo hacia el lado seguro.
8. **Diseño**: `CatalogSolution` y el puerto asíncrono `ExoplanetCatalog` en `domain/catalog.py`; `compare`, `CatalogComparison` y `TensionResult` en `domain/tension.py`; caso de uso `ComputeTensions` en `application/`, sin LLM, sin umbral y sin capturar excepciones del puerto. Sin red, persistencia ni adaptador (T74, T72).

## Consecuencias

- Un paper que discrepa de la solución por defecto pero coincide con otra previa es candidato; la redacción (T76) debe decirlo, y los σ frente a todas las previas lo permiten.
- La regla de "todas en tensión" hace que el resultado no dependa del modelo que el paper elija como principal, a costa de perder casos como V1298 Tau e.
- La exactitud de la exclusión de la solución propia depende de que T74 extraiga `arxiv_id` en el formato de `Item.external_id`.

## Alternativas descartadas

- **Referencia = mínimo frente a todas las previas (rango)**: una sola previa discrepante oculta cualquier tensión (V1298 b: 0,04σ).
- **Referencia = máximo**: falsos positivos en planetas con previas heterogéneas.
- **Media ponderada de las previas**: la domina la previa más precisa y esconde que las previas no concuerdan entre sí.
- **Media de los dos errores o el mayor**: más simples, pero sesgan el σ en medidas asimétricas.
- **Umbral 2σ** (ruido por búsqueda múltiple) o **5σ** (ningún caso en los datos disponibles).
- **Cada solución del paper por separado**: varios candidatos por planeta y un redactor más caro; **la de menor error**: depende del modelo elegido por el paper.
