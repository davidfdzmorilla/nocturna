# ADR 0021 · Criterio de cierre de fase 2

Fecha: 2026-11-03 · Estado: aceptado · Tarea: T86 · **Supersede** ADR 0012 §9 · **Resuelve** ADR 0013 §3 · Relacionados: ADR 0019 y ADR 0020 (remiten aquí), ADR 0029, ADR 0034, ADR 0038

## Contexto

ADR 0012 §9 cerraba la fase 2 con al menos 10 candidatos publicados en dos semanas. ADR 0013 §3 dejó ese criterio en revisión: el experimento T71 halló una tensión real en cinco noches. El criterio nuevo se pidió a partir del informe de T74 y del estudio de viabilidad de la opción 3(b) (decisión del autor del 2026-10-01): dos criterios, (a) y (b).

El alcance de T86 en `PLAN_TAREAS.md` añade un tercero, (c), sobre el histórico semanal del archivo (ADR 0019).

ADR 0019 cita «ADR 0020 (T86)» para este criterio. La renumeración del 2026-10-02 lo asignó a este ADR (ADR 0020 §Contexto).

ADR 0012 §9 y ADR 0013 §3 no se editan. La pregunta abierta de T78 («criterio de cierre», `OPEN_DECISIONS.md`) queda resuelta aquí.

## Decisión

Decisión del autor (plan de T86, aprobado el 2026-11-03): D1–D7, D9 y D10 con las recomendaciones del plan. D8 queda sin resolver de forma expresa; ver «Consecuencias».

La fase 2 cumple su criterio de cierre cuando se cumplen (a), (b) y (c). Los tres se miden sobre la base real, en solo lectura.

**(a) Cadena de extremo a extremo, un caso real por vía.** Un caso cumple si su resultado llega al Editor y recibe una decisión válida: el `Finding` del caso existe, en el mismo `Run` en que se creó hay un `AgentCall` del rol `editor` con `status = ok`, y el `Finding` tiene decisión (`published_at` no nulo, o nulo tras esa llamada válida del Editor: descartado). Se publique o no (D4). Casos fijados: V1298 Tau b, por la vía arXiv, y HD 202206 c, por la vía del archivo. Los dos recorrieron la cadena, así que no hizo falta sustituir ninguno por otro caso real de la misma vía (D5: solo con visto bueno expreso del autor).

**(b) Resultados publicados según el Editor en cuatro semanas.** Ventana: semanas ISO W41 a W44 de 2026, del 2026-10-05 al 2026-11-01 inclusive, con las fechas en `Europe/Madrid` sobre `findings.published_at` (D1).

- Cuentan los `Finding` de tipo `catalog_tension` y `solution_tension` con `published_at` no nulo (D2). `primera_medida` y `confirmacion_independiente` se informan aparte y no cuentan.
- Se cuentan resultados distintos por (planeta canónico del archivo, parámetro). El mismo resultado por las dos vías cuenta una vez (D3).
- Cumple con N = 2 resultados distintos.

**(c) Histórico del archivo de al menos cuatro semanas.** Cumple si:

- el primer y el último `archive_snapshot` distan al menos 28 días;
- `archive_default_change` tiene al menos una fila.

Cuentan los snapshots vacíos (D6). Cuenta cualquier fila de `archive_default_change`, incluidos los planetas nuevos sin solución previa; los cambios reales (previa y nueva no nulas) se informan aparte (D6). El histórico empieza con la carga completa del 2026-10-01 (D7).

**Cláusula de respaldo (del alcance de T86).** Si (b) falla con (a) y (c) cumplidos, la fase cierra igualmente, con el resumen semanal del archivo (ADR 0028) como producto principal. No aplica: (b) se cumple.

**Qué pasa si (a) o (c) fallan.** Este ADR no lo fija (D8, abierta; ver «Consecuencias»).

## Datos medidos

Medición del 2026-11-03 sobre la base real, con consultas de solo lectura.

Nota de procedencia: las consultas B3, C1 y C2 de esta sección se ejecutaron tal cual el 2026-11-03 en una sesión `READ ONLY` contra la base real (`docker exec -i nocturna-postgres psql -U nocturna -d nocturna`) y reproducen las cifras de la medición. El resto de datos (criterio (a), totales por tipo, redactor, noches) sale de consultas de solo lectura de la misma fecha sobre `tension_evaluation`, `solution_tension`, `findings`, `agent_calls` y `runs` (excluidos los `Run` con `notes = 'reread'`).

### Criterio (a)

| Vía | Caso | Cadena | Resultado |
|---|---|---|---|
| arXiv | V1298 Tau b (ítem 2609.30038, `paper_explained` published) | `reader-v2` y `popularizer-v2` el 2026-09-27; relectura `reader-v3` el 2026-10-06; `TensionEvaluation` de masa `evaluated` a 3,37 σ | `writer-v1` (8.584 tokens, ok) y `editor-v3` (17.956 tokens, ok) la noche del 2026-10-09. `catalog_tension` e601a5b6-279a-48ef-a7bf-fe917a7059ab **no publicada**: el Editor la descartó |
| Archivo | HD 202206 c | `solution_tension` de masa a 8,20 σ (Cao et al. 2026, `releasedate` 2026-08-20, frente a Benedict & Harrison 2017, `reference_role = most_recent`, sin etiquetas) | Publicada el 2026-10-16 a las 00:13 con confianza 0,70 (f069f738-4d9f-475c-b636-7c631b5f57d1). `writer-archive-v1` y `editor-v4` ok las noches del 2026-10-16 (6.001 y 11.988 tokens) y 2026-10-19 (6.127 y 12.502). El periodo, a 3,46 σ, se redactó y lo descartó el Editor |

### Criterio (b)

Consulta B3, de referencia:

```sql
-- B3: resultados publicados en la ventana W41–W44 (2026-10-05 a 2026-11-01, Europe/Madrid)
WITH base AS (
    SELECT f.id, f.type, f.published_at,
           coalesce(te.archive_planet_name, te.planet_name) AS planeta,
           te.parameter::text AS parametro
    FROM findings f
    JOIN tension_evaluation te ON te.id = f.tension_evaluation_id
    WHERE f.type = 'catalog_tension'
    UNION ALL
    SELECT f.id, f.type, f.published_at,
           st.planet_name, st.parameter::text
    FROM findings f
    JOIN solution_tension st ON st.id = f.solution_tension_id
    WHERE f.type = 'solution_tension'
),
ventana AS (
    SELECT * FROM base
    WHERE published_at IS NOT NULL
      AND (published_at AT TIME ZONE 'Europe/Madrid')::date
          BETWEEN DATE '2026-10-05' AND DATE '2026-11-01'
)
SELECT type::text, count(*) FROM ventana GROUP BY type
UNION ALL
SELECT 'resultados_distintos', count(*)
FROM (SELECT DISTINCT planeta, parametro FROM ventana) d;
```

Resultado, W41–W44:

| Tipo | Publicados | Resultados distintos |
|---|---|---|
| `catalog_tension` | 1 | — |
| `solution_tension` | 6 | — |
| Total | 7 | **6** |

Hay 6 resultados distintos frente a N = 2. HD 65216 c (periodo) se publicó por las dos vías: `catalog_tension` el 2026-10-09 (confianza 0,80) y `solution_tension` el 2026-10-17 (confianza 0,65). Cuenta una vez (D3).

Ventanas alternativas, solo informativas: 2026-10-15 a 2026-11-11 (medida hasta el 2026-11-03, fecha de la medición), 6 publicados y 6 distintos; 2026-10-07 a 2026-11-03, 7 publicados y 6 distintos. El resultado no depende de la ventana.

Publicados de tensión, todos en W41–W42:

| Vía | Planeta | Parámetro | Publicado | Confianza |
|---|---|---|---|---|
| catalog | HD 65216 c | periodo | 2026-10-09 | 0,80 |
| solution | HD 202206 b | masa | 2026-10-15 | 0,60 |
| solution | HD 114082 b | periodo | 2026-10-15 | 0,60 |
| solution | TOI-4504 c | masa | 2026-10-16 | 0,55 |
| solution | HD 202206 c | masa | 2026-10-16 | 0,70 |
| solution | HD 65216 c | periodo | 2026-10-17 | 0,65 |
| solution | TOI-4504 d | masa | 2026-10-18 | 0,75 |

Ninguna tensión publicada después del 2026-10-18.

Totales históricos por tipo (creados, publicados, descartados por el Editor), sin ventana: `catalog_tension` 3, 1, 2; `solution_tension` 10, 6, 4; `primera_medida` 14, 14, 0; `confirmacion_independiente` 3, 3, 0. Ninguno queda sin decisión del Editor.

Aparte, no cuentan (D2): en W41–W44 se publicaron 14 `primera_medida` y 3 `confirmacion_independiente`. La última `primera_medida` es TIC 402710280 b (radio), del 2026-10-22.

Redactor y evaluaciones:

- `writer-v1`: 3 llamadas (2026-10-09 ×2, 2026-10-10). `writer-archive-v1`: 10 llamadas (2026-10-15 a 2026-10-19, 2 por noche, 6.000–7.200 tokens cada una). `writer-digest-v1`: 2 llamadas el 2026-11-03 (26.425 tokens). Todas ok.
- Solution tensions elegibles: 10; sin redactar: 0.
- `tension_evaluation`: `evaluated` 10 (3 con σ ≥ 3), `awaiting_reference` 9, `closed_loop` 11, `consistent_with_limit` 1.

### Criterio (c)

Consulta C1, de referencia:

```sql
-- C1: snapshots del archivo, con filas de cambio y altas por snapshot
SELECT s.taken_at AT TIME ZONE 'Europe/Madrid' AS hora_local,
       s.kind, s.rows_total, s.defaults_total, s.max_releasedate,
       (SELECT count(*) FROM archive_default_change c WHERE c.snapshot_id = s.id) AS filas_cambio,
       (SELECT count(*) FROM archive_solution a WHERE a.first_seen_snapshot_id = s.id) AS altas
FROM archive_snapshot s
ORDER BY s.taken_at;

-- C1 (extensión): primer y último snapshot, y semanas ISO distintas
SELECT min(taken_at), max(taken_at), max(taken_at) - min(taken_at) AS extension,
       count(DISTINCT date_trunc('week', taken_at AT TIME ZONE 'Europe/Madrid')) AS semanas_iso
FROM archive_snapshot;
```

Consulta C2, de referencia:

```sql
-- C2: filas de archive_default_change
SELECT count(*) AS filas,
       count(*) FILTER (WHERE old_solution_key IS NOT NULL AND new_solution_key IS NOT NULL) AS cambios_reales,
       count(*) FILTER (WHERE new_solution_key IS NULL) AS perdidas,
       count(*) FILTER (WHERE old_solution_key IS NULL) AS sin_previa
FROM archive_default_change;
```

Resultados:

| Snapshot (hora local) | Filas de cambio | Altas | Notas |
|---|---|---|---|
| 2026-10-01 13:27 (completo) | 0 (primer snapshot, no registra cambios) | — | 40.031 filas, 6.372 soluciones por defecto; incremental el mismo día |
| 2026-10-02 | 4 (1 cambio, 3 sin previa) | 7 | — |
| 2026-10-09 | 70 (todas sin previa) | 142 | — |
| 2026-10-16 | 10 (4 cambios, 6 sin previa) | 17 | `max_releasedate` 2026-10-15 |
| 2026-10-23 | 0 | 0 | vacío |
| 2026-10-30 | 0 | 0 | vacío |

Las altas son los `archive_solution` con `first_seen_snapshot_id` en ese snapshot.

- Extensión: del 2026-10-01 11:27 UTC al 2026-10-30 09:00 UTC, 28 días y 21 horas. 5 semanas ISO con snapshot (W40 a W44).
- `archive_default_change`: 84 filas, de ellas 79 sin previa, 5 cambios reales y 0 pérdidas.
- `max_releasedate` se queda en 2026-10-15 desde el snapshot del 2026-10-16. El archivo no publica desde esa fecha.

### Incidencias del periodo (solo contexto)

Estas incidencias no cambian la ventana de (b) ni el criterio (D10).

- El archivo no publica desde el 2026-10-15. Los snapshots de 10-23 y 10-30 salieron vacíos.
- Servicio de alias del archivo colgado del 2026-10-22 al 2026-10-29. `evaluate-tensions` salió con código 1 y luego con 4 (T99, ADR 0037).
- Noches (fecha local de inicio) 2026-10-29 y 2026-10-30 `partial`, con 0 tokens: 429 de la suscripción en el Reader y después circuit breaker por `agent_error`.
- Noches 2026-10-15, 10-23, 10-24, 10-31 y 11-01 `partial` por `ArxivUnavailable` en la ingesta.
- Huecos sin `Run`: 2026-10-11 a 10-13.

## Resultado por criterio

| Criterio | Umbral | Medido | Estado |
|---|---|---|---|
| (a) | un caso real por vía con cadena completa | V1298 Tau b (arXiv, descartado por el Editor); HD 202206 c (archivo, publicado) | cumple |
| (b) | N = 2 resultados distintos publicados en W41–W44 | 6 resultados distintos (7 publicados) | cumple |
| (c) | ≥ 28 días de histórico y ≥ 1 fila en `archive_default_change` | 28 d 21 h, 5 semanas ISO; 84 filas (5 cambios reales, 0 pérdidas) | cumple |

## Consecuencias

- **T78 queda pendiente** y pasa a ser la valoración del autor: cuántos candidatos son genuinamente interesantes y si la fase sigue por esta vía o cambia de enfoque. La decisión final irá en un ADR nuevo, el próximo número libre (0039). T78 no cambia de estado en esta tarea.
- Observación para T78, sin decidir nada: todas las tensiones publicadas son de W41–W42. Desde el 2026-10-18 no hay ninguna tensión publicada. El último snapshot con altas es el del 2026-10-16; los de 10-23 y 10-30 salieron vacíos (el archivo no publica desde el 2026-10-15). La vía de arXiv no ha producido ninguna tensión elegible nueva.
- **D8 (abierta).** La tarea no define qué pasa si fallan (a) o (c). La propuesta del plan es que la fase no cierre y se abra una tarea de corrección. No la ha aprobado el autor de forma expresa. No aplica a esta medición, porque (a) y (c) se cumplen. Registrada en `OPEN_DECISIONS.md` con el id T86.
- Sin cambios de código, de configuración ni de migración. Sin efecto en `budget.py`.

## Alternativas descartadas

- **Mantener ADR 0012 §9 (10 candidatos en dos semanas):** el ritmo observado en T71 fue de una tensión real en cinco noches (ADR 0013 §3).
- **Contar `primera_medida` y `confirmacion_independiente` en (b):** son hallazgos de medida sin discrepancia; el criterio cuenta solo tensiones publicadas (D2).
- **Contar hallazgos en lugar de resultados:** HD 65216 c aparece dos veces (una por vía) y se contaría doble (D3).
- **Ventana de (b) cambiada tras medir:** el resultado no depende de ella (ventanas alternativas en «Datos medidos»); se mantiene W41–W44 (D1) y D10 prohíbe ampliarla o recortarla por incidencias.
