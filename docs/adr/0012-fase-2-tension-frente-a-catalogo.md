# ADR 0012 · Fase 2: tensión de un objeto frente a un catálogo, con contraste determinista

Fecha: 2026-09-28 · Estado: aceptado (respuestas del autor confirmadas el 2026-09-28; plan de tareas T70–T78 aprobado en T61 el mismo día) · **Resuelve** la entrada "¿Qué cuenta como descubrimiento?" de `OPEN_DECISIONS.md` · **Matiza CLAUDE.md** § Qué es este proyecto (ingesta por MCP)

## Contexto

La fase 1 publica `paper_explained`: el mismo abstract, explicado en tres niveles. Demuestra que el pipeline funciona (ingesta, Reader, Popularizer, Editor, tope nocturno, web), pero no responde a la pregunta de fondo del proyecto: cómo puede Claude hacer descubrimientos. T61 tenía que definir qué es un descubrimiento antes de diseñar nada nuevo.

## Decisión

1. **Qué cuenta como descubrimiento (P1)**: la tensión o novedad de un objeto de un paper frente a un catálogo, siempre con contraste determinista. Ejemplo: el paper da una masa X; el catálogo tiene N medidas previas en otro rango; la discrepancia se calcula en σ. No es "descubrimiento científico" en sentido estricto, sino información nueva y verificable. Se publica como un `Finding.type` nuevo (`catalog_tension`) ligado a `item_id`, sin entidad nueva.
2. **Fuente (P2)**: NASA Exoplanet Archive, por TAP, sin autenticación, sobre la tabla con múltiples soluciones publicadas por planeta. Es la única fuente de la fase 2.
3. **Cálculo (P3)**: Python determinista consulta el catálogo y calcula. Claude solo interpreta y redacta a partir de números ya calculados. Ningún agente recibe herramientas (`tools=[]`, ADR 0006). Por tanto, la deuda de `last_result` con varios frames (`TECHNICAL_DEBT.md`) no es dependencia de la fase 2; sigue registrada para el día que haya herramientas.
4. **Presupuesto (P4)**: `budget.nightly_tokens` no sube de 300.000. La etapa nueva tiene una reserva fija, análoga a la del Editor. Su valor inicial lo fija el experimento de viabilidad (orden de 20–40k, provisional). `limits.max_items_per_night` baja de 40 a 30. La consulta al catálogo no gasta tokens; Claude solo se llama cuando hay tensión calculada.
5. **`paper_explained` se mantiene (P5)**. El Reader alimenta el cruce con `objects`.
6. **Filtro de publicación (P6)**: el contraste determinista es obligatorio; sin discrepancia calculada no se publica. Cada afirmación va acompañada de sus números y del enlace al archivo. La web lleva la etiqueta "candidato". Sin revisión humana.
7. **Despliegue (P7)**: ninguno en fase 2; todo local.
8. **Web (P8)**: el mismo feed, con etiqueta visible por tipo y filtro por tipo. Idioma: castellano.
9. **Cierre de la fase 2 (P9)**: dos semanas de noches con el cruce activo y al menos 10 candidatos publicados. El autor valora cuántos son genuinamente interesantes y decide si sigue esta vía o cambia de enfoque.
10. **Orden**: primero T70, el bug de `page` sin tope (`api/routes/findings.py:35`). Después T71, un experimento de viabilidad sin Claude y sin tokens: cruzar solo con Python los ítems de astro-ph.EP ya en BD con el Exoplanet Archive, y medir cuántos objetos casan y cuántas tensiones reales aparecen. Si sale cero, se replantea antes de construir más.
11. **Valor numérico del paper**: el Reader solo extrae `objects`, y el cálculo necesita el valor que da el paper. T71 mide dos vías sin tokens: (a) parser determinista sobre el abstract y sobre `Reading.claims`; (b) la solución del propio paper si el archivo ya la tiene ingerida. La vía (c), que el Reader extraiga cantidades estructuradas, solo se valora si (a) y (b) recuperan pocos casos, y requiere decisión expresa del autor.
12. **Los `catalog_tension` pasan por el Editor**. `Finding.publish()` y el CHECK de BD exigen `confidence`, que solo asigna el Editor. Se mantiene un único punto de decisión de publicación y la regla de una sola llamada al Editor por noche: recibe los dos tipos de candidato en esa llamada.
13. **Números y enlaces en un campo estructurado de `Finding`**, no dentro del texto. Los números salen tal cual del cálculo de Python; el texto de Claude solo los comenta. La forma exacta del campo se diseña en T72.
14. **MCP**: el Exoplanet Archive se integra como adaptador de `infrastructure/` sin servidor MCP, porque ningún agente lo consume. Los servidores MCP propios quedan para fuentes que consulte un agente.

## Consecuencias

- Aparece un `Finding.type` nuevo, un campo estructurado nuevo en `Finding` y un rol de agente nuevo que redacta; el rol pasa por `BudgetGuard` y `AgentRunner` como los de la fase 1.
- `BudgetGuard` gana una segunda reserva fija. Reader y Popularizer disponen de menos presupuesto por noche; lo compensa en parte la bajada de `max_items_per_night` a 30.
- El Editor recibe más candidatos en su única llamada; su validador de reserva debe contarlos.
- La web muestra dos tipos de hallazgo en el mismo feed, distinguibles y filtrables.
- Todo lo que viene después de T71 depende de su resultado.

## Alternativas descartadas

- **Anomalías poblacionales como definición de descubrimiento**: fuera de la fase 2 por el riesgo de búsqueda múltiple.
- **Subir `nightly_tokens` para la etapa nueva**: descartado; se mantiene 300.000 y la etapa vive de una reserva propia.
- **Claude consultando el catálogo con herramientas, o ejecutando código**: descartado por gasto, por la deuda de contabilidad multi-frame y por riesgo de cifras inventadas.
- **Números dentro del texto del hallazgo**: dependería de que Claude los copie bien y la web no podría mostrarlos de forma fiable.
- **Publicar `catalog_tension` sin pasar por el Editor**: exigiría otro mecanismo para asignar `confidence` y un segundo punto de decisión de publicación.
