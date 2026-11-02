# ADR 0038 · Redactor del resumen semanal

Fecha: 2026-11-02 · Estado: aceptado · Tarea: T84.r · Relacionados: ADR 0028 §1 y §3, ADR 0025 §1, ADR 0030 §2, ADR 0035, ADR 0036

## Contexto

ADR 0028 dejó el resumen semanal del archivo sin LLM ni Editor (§1, §3) y pasó a T84.r el texto del redactor y la pregunta de dónde corre (OD de T75/T84.r).

Datos reales (base, 2026-11-02, solo lectura):

| Semana | Cambios de solución | Altas | Tamaño de `archive-digest` |
|---|---|---|---|
| W40 | 1 | 3 | 897 B |
| W41 | 0 | 70 (63 de un solo paper) | 6.834 B |
| W42 | 4 | 6 | 2.279 B |
| W43, W44 | 0 | 0 | — |

El archivo no publica desde el 2026-10-15 (`max(rowupdate)` por TAP), por eso W43 y W44 están vacías. El autor arrancó la tarea con 3 semanas con cambios en lugar de las 4 previstas.

Coste real del redactor: `writer-archive-v1` 6.585 tokens de media (máximo 8.098), `writer-v1` 7.194 (máximo 8.720), frente a `writer_estimated_tokens = 12.000`. El tope de 2 llamadas por noche no se usa desde el 2026-10-20.

## Decisión

Decisión del autor (T84.r): D1–D15 con las recomendaciones del plan y el refinamiento de D11 tras la revisión. Modifica ADR 0028 §1 (ya no es «sin LLM») y §3 (el texto se ve sin Editor), que no se editan, y aplica ADR 0025 §1.

1. **Dónde corre** (D1 a). Tercera pasada del redactor dentro de `run-night`, después de arXiv y del archivo y antes del Editor. Un `Run` propio nacería con el presupuesto entero de una noche, y `archive-snapshot` corre fuera de la ventana.
2. **Gasto** (ADR 0025 §1). Rol `writer`, la misma reserva de 24.000, el mismo tope de 2 llamadas por noche y la estimación `writer_estimated_tokens`. Pasa por `AgentRunner` y `BudgetGuard.authorize` en cada intento; `AgentCall` con `item_id` nulo y `prompt_version = writer-digest-v1`. Sin cambios en `budget.py`, en el reparto ni en el validador de la reserva del Editor.
3. **Orden y cortes** (D3 a). Cualquier parada de una pasada anterior corta el resumen. No corre si la noche ya se saltó el Editor (fuera de ventana, límite de uso). Las denegaciones se tratan en el mismo bucle que las tensiones (ADR 0030 §2): el tope termina la pasada sin degradar la noche; el presupuesto agotado da `partial` y el Editor sigue; fuera de ventana, `killed` sin Editor; límite de uso, `partial` sin Editor.
4. **Elegibilidad** (D2 a, D6 a, D7 a). Una semana ISO es elegible cuando está cerrada (lunes 00:00 en `window.timezone`), tiene al menos una transición, es igual o posterior a `[archive_digest] since_week` (`2026-W40`) y no tiene texto. Se escribe la más reciente primero. Una semana vacía no llama ni deja fila.
5. **No es un `Finding`** (D4 a). Tabla `archive_digest_text`, una fila por semana: estado `written` o `failed`, título y resumen, hash SHA-256 de la entrada, versión del prompt y `run_id`. El resumen semanal sigue calculándose al leer (ADR 0028 §2); el texto se le añade.
6. **Sin Editor** (D5 a). El texto se ve en cuanto se escribe, con etiqueta de IA y junto a las tablas deterministas. Es el primer texto de IA publicado sin ningún filtro.
7. **Sin reescrituras** (D8 a). Si los datos de una semana ya escrita cambian, el texto no se reescribe. `archive-digest --week` y el `--dry-run` lo marcan «desactualizado» al comparar el hash; la API lo sigue sirviendo.
8. **Fallos** (D11 a, refinado). Dos salidas inválidas dejan la semana `failed`, que no se reintenta. Si una salida inválida no puede reintentarse porque la autorización siguiente se deniega (tope, presupuesto, ventana o límite de uso), la semana también queda `failed` y la denegación sigue su curso sin cambios. Así ninguna semana gasta noche tras noche. Un error del agente o un timeout no deja fila y se reintenta la noche siguiente dentro del tope.
9. **Entrada** (D10 a). Bloque `<weekly_digest>` determinista, construido en Python: semana y fechas, recuentos por tipo, cada cambio con los valores redondeados (`round_to_error`) y el porcentaje (`percent_4sf`), y las altas, recuperaciones y pérdidas agrupadas por referencia con hasta 8 nombres. Todo texto libre pasa por `inline`; nunca lleva claves de solución, URLs ni fechas del archivo.
10. **Salida y prompt** (D9 a). `{title, summary}`: título de hasta 90 caracteres y resumen de 1 o 2 párrafos, hasta 220 palabras, en español. `writer-digest-v1` hereda las reglas de ADR 0035: nada de «pipeline», no mencionar el bloque ni los nombres de campo, las mismas cifras cambiando solo el punto por la coma, ejemplo inventado, sin URLs, sin causas inventadas, ningún valor presentado como el correcto y no llamar «descubrimiento» a la entrada de un planeta en el archivo.
11. **API.** `GET /archive/weeks/{week}` añade `writer_text` (`title`, `summary`) por lista blanca; es `null` sin texto o con la fila `failed`. Nunca salen la versión del prompt, el `run_id` ni el hash.
12. **Interruptor** (D12). `[archive_digest] writer_enabled = false` hasta cerrar la web (T84.r.w); lo activa el autor con un `chore(config)` aparte, fuera de 00:00–04:45.

## Consecuencias

- Coste esperado: una llamada por semana con cambios. Humo real del 2026-11-02 (CLI 2.1.316, SDK 0.2.153) con una semana tipo W42: `written` al primer intento, 4.975 tokens (el 41 % de la estimación). El relleno de W40 a W42 son 3 llamadas en 2 noches.
- El tope de 2 llamadas se comparte con las tensiones; una semana pendiente espera a una noche con el tope libre.
- Llega 3 días después del snapshot del viernes: la noche del lunes es la primera útil.
- Nuevo camino hacia Claude, por la misma puerta: `test_llm_call_sites.py` sigue con una sola entrada.
- La web todavía no muestra el texto (T84.r.w); hasta entonces la nota «sin texto de IA» de `/archivo/[semana]` sigue siendo cierta.
- Migración `a4d9e1b7c352` (tabla `archive_digest_text`; el downgrade se niega si hay filas).

## Alternativas descartadas

- **`Run` o comando propio:** duplicaría `nightly_tokens` la noche que corriera.
- **Dentro de `archive-snapshot`:** fuera de la ventana; el guard lo deniega.
- **Elegible tras el snapshot del viernes, con reescritura:** gasto repetido y textos que cambian.
- **Tipo de `Finding` `weekly_digest` con Editor:** contradice ADR 0028 §2 y metería en el feed algo que no es un hallazgo.
- **Sub-tope propio de 1 llamada:** tocaría `budget.py` para un caso que el tope compartido ya cubre.
- **Tres niveles de texto:** la página no tiene selector y ya muestra el detalle en tablas.
