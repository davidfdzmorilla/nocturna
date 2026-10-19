# ADR 0035 · `writer-archive-v2`: redondeo y porcentaje en el bloque del redactor del archivo

Fecha: 2026-10-19 · Estado: aceptado · Tarea: T96 · Relacionados: ADR 0026, ADR 0030 §5

## Contexto

`writer-archive-v1` (T85.e, ADR 0030) redactó 10 `solution_tension` entre el 2026-10-14 y el 2026-10-18. Los leí todos (consulta de solo lectura, 2026-10-19):

| Patrón | Textos |
|---|---|
| «pipeline» como jerga («umbral aplicado por el pipeline») | 8 de 10 |
| Menciona «el bloque» o explica que no hay etiquetas | 4 de 10 |
| Valores en bruto con 8 decimales («1197,32317485 (+57,52694167/-57,90833576)») | 4 de 10 |
| Diferencia relativa como fracción («diferencia relativa de 1,0551») | 1 de 10 |
| `\n` literal en el texto | 1 de 10 |

Dos de esos patrones vienen del bloque, no del estilo del modelo:

- **La fracción.** El bloque daba `relative_difference=1.0551` como fracción, y el prompt prohíbe calcular otras cifras. Para dar un porcentaje, el modelo tenía que saltarse esa regla.
- **Los decimales.** El bloque daba los valores con `repr(float)`, con todos los decimales que trae el archivo, a menudo tras una conversión de unidades.

v1 además incumplía ADR 0030 §5: su ejemplo de coma decimal usaba cifras de un caso real («982,09; 8,2 σ», HD 202206 c).

El `\n` literal (una barra invertida seguida de `n` en el texto guardado) viene de la regla «usa `\n` escapado entre párrafos», común a los dos redactores: el modelo a veces lo escapa dos veces. Lo tienen 1 de 3 `catalog_tension` (`writer-v1`) y 1 de 10 `solution_tension`, ninguno publicado, y el humo de v2 lo reprodujo. El Popularizer no lo tiene (0 de 293).

## Decisión

Decisión del autor (T96): D1 a, D2 b, D3 (reglas 1 y 2, y 3a), D4 con humo, D5, D6 con este ADR y D7. Además, el arreglo del `\n` literal. Modifica ADR 0030 §5 en lo que da el bloque, que no se edita.

1. **Versión (D1 a).** `prompts/writer-archive-v2.md` es un fichero nuevo, y `prompt_loader.WRITER_ARCHIVE_PROMPT_VERSION` pasa a `writer-archive-v2`.
   - v1 se conserva con su hash congelado en un test. Los `AgentCall` y hallazgos históricos siguen citándola.
   - Ningún literal de versión del archivo vive fuera de `prompt_loader.py`, para que el prompt cargado y el `prompt_version` registrado no puedan divergir.
2. **Porcentaje (D2 b).** El bloque da `relative_difference_pct`, calculado en Python con 4 cifras significativas y sin notación exponencial, en lugar de la fracción. La razón (`ratio`) se mantiene.
3. **Redondeo (D3, 3a).** El bloque redondea el valor y los errores de cada solución: la nueva, la de referencia y las demás. Usa los decimales necesarios para que el error menor tenga 2 cifras significativas, y los mismos en valor y errores.
   - Nunca menos de 0 decimales.
   - Si falta un error o el menor es 0, el valor va como viene.
   - Lo hace la función de presentación `prompt_text.round_to_error`.
4. **Reglas nuevas del prompt (D3).**
   - No usar «pipeline»: decir «el análisis automático».
   - No mencionar el bloque, los datos de entrada ni su formato.
   - Si no hay etiquetas, no hablar de etiquetas.
   - Contar la diferencia relativa como porcentaje con la cifra dada.
   - Usar los valores tal como vienen redondeados.
   - El ejemplo de coma decimal usa cifras inventadas.
5. **Salto de línea.** `writer_output.WriterOutput` convierte un `\n` literal en un salto de línea real antes de comprobar que el campo no está vacío. Vale para los dos redactores (`writer-v1` y `writer-archive-v2`) sin cambiar ningún prompt.
6. **Sin cambios.**
   - `writer_estimated_tokens` sigue en 12.000 (D5).
   - El Editor (`editor-v4`) y su línea `data` no cambian.
   - Los textos ya guardados con v1 se quedan como están (D7).

## Consecuencias

- **Tokens.** El prompt crece unos 535 caracteres, del orden de 150–200 tokens por intento, y el bloque se acorta cuando había decimales largos. El humo del 2026-10-19 (HD 202206 c, masa) gastó 7.010 tokens en un intento, frente a 7.093 con v1 en T85.e. Escribió los valores redondeados («982 (+44/-44)») y la diferencia relativa en porcentaje («82,74%»), sin «pipeline», sin etiquetas y sin mencionar el bloque.
- **El Editor ve otro formato.** Sigue viendo la diferencia relativa como fracción y los valores sin redondear, mientras el texto que juzga los da en porcentaje y redondeados. Redondear no afirma más que los números. Si alguna noche el Editor penaliza la discrepancia, lo propio es un `editor-v5` con el porcentaje.
- **Errores grandes.** Con un error menor de 100 o más, el error sale con todas sus cifras enteras (3 o más). Si el redondeo sube de orden de magnitud (0,0996 → 0,100), salen 3 cifras.
- **Una barra invertida seguida de `n`** que no fuera un salto se convertiría también en salto de línea. Los prompts piden prosa en español, sin LaTeX.
- `night_report.sql` Q9b separa las llamadas por versión de prompt sin cambios.

## Alternativas descartadas

- **Pedir al modelo que multiplique por 100 (D2 a):** choca con «no calcules otras cifras» y deja la aritmética al LLM.
- **Pedir el redondeo en el prompt (3b):** el modelo tendría que decidir cuántos decimales usar. En Python es determinista.
- **Clave de versión en `pipeline.toml` (D1 b):** añade configuración sin necesidad. Ningún otro prompt se versiona así.
- **Arreglar el `\n` en los prompts:** obliga a sacar `writer-v2` y `writer-archive-v3`, y no garantiza nada. La normalización en Python sí.
- **Volver a redactar los textos de v1:** cuesta tokens y contradice la política de no volver a redactar (ADR 0030).
