# ADR 0039 · Decisión final de fase 2

Fecha: 2026-11-03 · Estado: aceptado · Tarea: T78 · Relacionados: ADR 0012, 0013, 0020, 0021, 0028, 0034, 0038

## Contexto

ADR 0021 fijó el criterio de cierre de la fase 2 en tres condiciones, (a), (b) y (c), y las midió el 2026-11-03 sobre la base real. Las tres se cumplen. ADR 0021 dejó la valoración del autor y la decisión final de la fase para T78, con ADR nuevo.

Corte de datos: noche del 2026-11-03. Consultas de solo lectura sobre la base real, ejecutadas entre las 09:30 y las 10:00 (Europe/Madrid), fuera de la ventana de ejecución.

## Datos

El material completo está en [`docs/CIERRE_FASE_2.md`](../CIERRE_FASE_2.md): registro por noche (§1), muestreo de atribución (§2), comprobaciones contra el archivo (§2.b), fichas de valoración (§3) y consultas de referencia. Resumen:

- **Registro (§1).** 32 noches (24 `completed`, 8 `partial`) y 5 relecturas de T82. 122 lecturas con `reader-v3` en noches. Medidas guardadas: 22 en noches (21 utilizables) y 21 en relecturas (18 utilizables). Desde el 2026-10-10 solo dos noches tienen medidas (2026-10-21 y 2026-11-03). Tokens de más del Reader v3: unos 240.000 en noches, unos 2.000 por lectura; cifra aproximada (§1, limitaciones). Redactor: 15 llamadas y 113.852 tokens, todas `ok`. Editor: una llamada por noche, salvo el 2026-10-17, con un `invalid_output` y el reintento permitido. Dos noches (2026-10-29 y 2026-10-30) no gastaron tokens.
- **Vía del archivo (§1).** 99 tensiones calculadas el 2026-10-14 (8 elegibles) y 7 el 2026-10-16 (2 elegibles). Desde entonces no hay cálculo nuevo: el archivo no publica desde el 2026-10-15.
- **Publicados (§1).** 7 tensiones, todas entre el 2026-10-09 y el 2026-10-18. 17 hallazgos de medida, el último el 2026-10-22.
- **Muestreo (§2).** 20 abstracts no vistos: censo de las 10 lecturas `reader-v3` con medidas, salvo los 4 abstracts de T71.b (estratos S1 y S2) y 10 de 94 lecturas sin medidas con `exoplanet_match` cierto (S3). 32 medidas revisadas: 31 correctas. Una `m sin i` (TOI-2211 c) se guardó como masa y se publicó. En S3, 1 caso dudoso de 10 (61 Cygni, candidato sin designación); no se publicó nada de él.
- **Comprobación contra el archivo (§2.b).** Se confirman los veredictos de §2. De los 14 `primera_medida`, 11 son de planetas ausentes del archivo (`absent`), que repiten el paper de descubrimiento; solo 3 son primeras medidas de planetas catalogados (`no_comparable_solution`). `confirmacion_independiente` no exige otro equipo: TOI-2158 b se «confirma» con Knudstrup et al. 2022 a partir de Knudstrup et al. 2026, mismo primer autor.
- **Valoración (§3).**

  | Grupo | Interesantes / total |
  |---|---|
  | `catalog_tension` (arXiv) | 2 / 3 |
  | `solution_tension` (archivo) | 5 / 10; 5 / 6 entre las publicadas (T8 duplica a T1) |
  | `primera_medida` | 3 / 14 |
  | `confirmacion_independiente` | 0 / 3 |

  Resultados de tensión distintos e interesantes: 7, en 5 sistemas (HD 65216, V1298 Tau, HD 114082, HD 202206 y TOI-4504). Seis están publicados; V1298 Tau b fue descartado por el Editor. El Editor coincide con la valoración en 11 de 13 tensiones (discrepan T2 y T8).
- **Errores publicados.** M12 (TOI-2211 c, `m sin i` de un candidato sin designación; `ab6940a9-5618-4192-ad1f-17db2c02d52f`), M14 (TIC 402710280 b, candidato presentado como planeta; `53276767-4ce1-4940-8a0a-002d1cfe1eb8`) y M15 (confirmación de TOI-2158 b entre artículos del mismo primer autor; `30365a4f-dd57-4f52-880b-99c35ca3d3a2`).

Las consultas de referencia de `CIERRE_FASE_2.md` no se incorporan a `night_report.sql`.

## Decisión

Decide el autor, 2026-11-03. Aprueba las recomendaciones del plan de T78 (D1–D10) y elige **O1 + O4 + T100**:

1. **O1.** Se mantienen las dos vías de tensión, la de arXiv (`catalog_tension`) y la del archivo (`solution_tension`), en operación continua, sin cambios de configuración.
2. **O4.** El resumen semanal del archivo (ADR 0028 y ADR 0038) pasa a ser el producto principal y estable de la web. Las tensiones son esporádicas, porque dependen de que el archivo publique. T78 no cambia código por esta decisión; si la web o la API necesitan algo para ello, es tarea aparte.
3. **T100.** Se abre la tarea de corrección de los hallazgos de medida: `primera_medida` solo para planetas catalogados sin valor comparable; ni `m sin i` ni candidatos sin designación como masa; `confirmacion_independiente` exige referencia de otro equipo; mecanismo para los hallazgos ya publicados. Alcance en `PLAN_TAREAS.md`.
4. **Errores publicados.** M12, M14 y M15 se registran y no se despublican (D8 del plan de T78). Su tratamiento queda en T100.
5. **Fase 2 cerrada.** Con (a), (b) y (c) cumplidos (ADR 0021), este ADR es la decisión final de la fase.
6. **Valoración.** Las marcas de §3 y las comprobaciones de §2.b las hizo Claude por encargo del autor, que delegó esa comprobación y aceptó sus recomendaciones de opción. El autor puede cambiar cualquier marca.

## Consecuencias

- T78 pasa a `done`. La fase 2 queda cerrada.
- Sin cambios de configuración (O1), de código (O4 en T78), de migración, de `budget.py` ni de `night_report.sql`.
- T100 queda abierta y depende de T78. Mientras T100 no esté hecha, la generación de hallazgos de medida sigue activa y los tres hallazgos erróneos siguen publicados: no existe mecanismo de retirada. El autor puede cortar la generación con `[measurement_findings] max_candidates_per_night = 0` en un `chore(config)` aparte. Esa opción no se decide aquí y queda en `OPEN_DECISIONS.md`.
- El muestreo cubre todas las lecturas `reader-v3` con medidas salvo los 4 abstracts de T71.b, pero solo 10 de las 94 sin medidas con `exoplanet_match`. La atribución de esas 84 restantes no está comprobada. La cifra de tokens de más del Reader v3 es aproximada.
- ADR 0021 D8 (qué pasa si fallan (a) o (c)) queda sin objeto: no aplica a la medición del 2026-11-03.

## Alternativas descartadas

- **O2. Solo la vía del archivo y el resumen** (`[reader] measurement_categories = []`, que apaga `reader-v3`, `catalog_tension`, `primera_medida` y `confirmacion_independiente`). Apaga la vía arXiv, que aporta 2 de 3 `catalog_tension` interesantes, y la de medidas, cuyo fallo se corrige en T100 sin apagarla.
- **O3. Solo la vía arXiv** (`[solution_tensions] writer_enabled = false`). Quita los 5 resultados interesantes que solo llegaron por el archivo (HD 114082 b, HD 202206 b y c, TOI-4504 c y d).
- **O5. Hallazgos de medida como producto principal.** Solo 3 de 14 `primera_medida` son interesantes, hay un error (M12) y un caso engañoso (M14), 0 de 3 confirmaciones son útiles y 11 de 14 recaen sobre planetas ausentes del archivo.
- **O6. Cambiar de enfoque** con una tarea de definición de la fase siguiente. Los datos no piden cambiar de enfoque: hay 7 resultados de tensión interesantes y el fallo medido está acotado en los hallazgos de medida (T100).
- **O7. Pausar `run-night` hasta decidir.** Detendría también el resumen semanal (tercera pasada del redactor, ADR 0038), que es el producto estable, sin necesidad: el error de medida se corta con la opción de configuración o con T100.
