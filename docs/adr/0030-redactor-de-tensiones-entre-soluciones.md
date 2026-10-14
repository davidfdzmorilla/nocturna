# ADR 0030 · Redactor y Editor de las tensiones entre soluciones

Fecha: 2026-10-14 · Estado: aceptado · Tarea: T85.e · Relacionados: ADR 0024 (contrato público), ADR 0025 (reserva del redactor), ADR 0026 (redactor de tensiones), ADR 0029 (tensiones entre soluciones)

## Contexto

T85 (ADR 0029) calcula y guarda las tensiones entre soluciones del archivo sin gastar tokens. Al cerrarse quedaban 8 elegibles con su `Item` en la base real. Esta tarea las convierte en hallazgos `solution_tension` con el redactor y el Editor, sin tocar `budget.py`. Hay un caso extremo real: HD 114082 b en periodo, con del Burgo et al. 2026 (225,5504 ± 0,0004 d, solución por defecto) frente a Zakhozhay et al. 2022 (109,75 +0,40/−0,37 d). La razón es 2,055 y la σ de 289,5 solo refleja lo pequeño que es el error del valor nuevo.

## Decisión

1. **Mismo rol y misma puerta** (ADR 0029 §12). El redactor usa el rol `writer` y el modelo `[models] writer`, y llama a través de `AgentRunner`. Comparte con arXiv el tope `max_writer_calls_per_night` y la reserva `writer_reserve_tokens`, que se cuentan en base por rol y `run_id`. La estimación es `writer_estimated_tokens` (D10). Los sitios que llaman a `run_agent` siguen siendo los mismos.
2. **Dos pasadas en `_phase_writer`**: primero arXiv y después el archivo (D3 de ADR 0029), con un único bucle que trata todas las denegaciones igual que en ADR 0026. Cualquier parada de la pasada de arXiv corta la del archivo (D14): tope, presupuesto, ventana, `Run` no vivo, límite de uso, error o fallo al seleccionar. Un fallo al seleccionar el archivo deja `partial` (`writer_error`), sin llamadas del archivo, y el Editor sigue.
3. **Interruptor** (D16 de ADR 0029, D7, D8).
   - Con `[solution_tensions] writer_enabled = false` no se carga `writer-archive-v1`, no se construyen la selección ni la redacción del archivo y no hay ningún `authorize` del archivo. El Editor sigue en `editor-v3`.
   - Con `true`, la pasada del archivo corre y el Editor pasa a `editor-v4`; lo decide `editor_prompt_version(config)` en `run-night`, en `run-item` y en el informe.
   - El interruptor no se activa antes de cerrar T85.w, para no publicar un tipo que la web no sabe pintar.
4. **Selección** (`SelectSolutionTensions`, solo lectura, sin proveedor ni guard).
   - Toma las filas elegibles con ítem y excluye, cada una con su motivo:
     - las ya redactadas, publicadas o no;
     - las de ítems con `max_calls_per_item` intentos `invalid_output` del redactor;
     - las que no forman un `SolutionTensionData` válido, por ejemplo si el umbral vigente supera la σ guardada.
   - Orden (D1): σ descendente, `releasedate` de la alta descendente e id.
   - Una alta con dos parámetros elegibles da dos hallazgos con el mismo `Item` (D2). La marca de fallida sigue siendo por ítem, como en ADR 0026.
5. **Lo que ve el redactor** (D3, prompt `writer-archive-v1`). Un bloque entre `<solution_tension>` y `</solution_tension>` con:
   - planeta y parámetro;
   - la solución nueva y la de referencia, con valor, errores asimétricos tal como vienen, unidad, si son la solución por defecto y el rol de la referencia;
   - σ y umbral;
   - la diferencia relativa y la razón entre los valores;
   - hasta 8 otras soluciones con su σ;
   - las etiquetas, cada una con una frase fija;
   - el mes de entrada de la alta en el archivo.
   
   No hay abstract, título del ítem ni `pl_pubdate`. El único texto de terceros son los nombres de las referencias, saneados a una línea sin `<` ni `>`. El prompt prohíbe inventar causas y presentar un valor como el correcto, pide nombrar las soluciones compatibles y no lleva cifras de casos reales en sus ejemplos (petición del autor). El título lo redacta el modelo (D9).
6. **σ extrema** (D5, opción c). Por encima de `[solution_tensions] extreme_sigma` (50; sin valor por defecto en código y mayor que el umbral) se añade la marca derivada `extreme_sigma`, que no se guarda. La reciben el redactor y el Editor. El redactor no pone la cifra de σ en el titular y cuenta el cambio de valor y la razón; el Editor valora el cambio de valor, no la σ. Se descartaron la exclusión por encima de un tope y la lista manual, porque HD 114082 b es un cambio de periodo real e interesante.
7. **Editor** (D4, `editor-v4`, que es `editor-v3` con un quinto tipo). Recibe una línea `data` con planeta, parámetro, las dos soluciones, σ, umbral, número de otras soluciones y su rango de σ, etiquetas, diferencia relativa y razón. Sigue haciendo una sola llamada por noche con todos los tipos. La reserva del Editor no crece: el validador ya cuenta `max_writer_calls_per_night`. El hallazgo solo se publica si el Editor lo aprueba la noche en que se redacta (D8).
8. **API** (D6, ADR 0024). `SolutionTensionOut` por lista blanca: planeta, parámetro, enlace a la ficha, umbral, σ, rol, etiquetas, las dos soluciones (referencia, valor, errores, unidad, si es la solución por defecto y `arxiv_id`), las otras con su σ y `new_releasedate`. Nunca `solution_key`, `soltype`, `pl_pubdate`, `ttv_flag`, `period_check` ni `computed_at`. `?type=` admite los cinco tipos, y `source_url` es `null` para los ítems del archivo.
9. **Operación** (D11, D13). `night_report.sql` separa las llamadas del redactor por versión del prompt (Q9b) y lista los `solution_tension` de la noche con el gasto contado una vez por ítem (Q9c). `run-night --dry-run` muestra la cola del archivo con sus motivos aunque el interruptor esté apagado.

## Consecuencias

- Hasta que se active el interruptor no cambia nada en la noche: ni gasto, ni prompt del Editor, ni publicaciones.
- Al activarlo, la cola inicial de 8 elegibles se vacía en unas 4 a 7 noches si arXiv no ocupa el tope; en régimen estable entra como mucho una por semana.
- Un hallazgo redactado que el Editor no llega a decidir no se vuelve a redactar (misma política que T76).
- Una fila calculada con un umbral inferior al vigente queda excluida para siempre como dato inválido.
- `editor-v4` es algo más largo que `editor-v3`; `editor_base_tokens` se revisará con datos reales tras activarlo.

## Alternativas descartadas

- **Un único hallazgo por alta con varios parámetros**: exige cambiar el esquema y el índice único.
- **`editor-v4` en el merge**: cambiaría sin necesidad el prompt de la llamada más cara mientras no hay candidatos del archivo.
- **Una estimación de tokens propia por prompt**: obligaría a tocar el validador tope × estimación ≤ reserva sin datos que lo justifiquen.
- **Excluir las σ extremas o una lista manual**: perdería casos reales como HD 114082 b.
