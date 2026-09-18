# Calibración — Nocturna Fase 1

Registro de noches ejecutadas y métricas observadas. Actualizado por el autor cada mañana tras `run-night`.

## Datos de línea base (pre-T60, humos reales)

Observaciones de pruebas manuales en desarrollo, antes de calibración real:

| Métrica | Valor | Notas |
|---|---|---|
| Reader tokens/ítem | ~3.056 | T41 real (con cache), 2026-09-17 |
| Popularizer tokens/candidato | ~4.560 | T42 real (sin reintento), 2026-09-17 |
| Popularizer con reintento | ~9.120 | T42 real (2 intentos), 2026-09-17 |
| Editor tokens (3 candidatos) | 3.381 | T43 real (Opus), 2026-09-17 |
| Gasto lateral CLI (Haiku)/sesión | ~1.163 | T42 real (variable entre versiones), CLI 2.1.274 + SDK 0.2.153 |
| Sesiones estimadas/noche | ~82 | 40 ítems × 2 turnos + 1 Editor + 2 overhead |
| Gasto lateral total/noche | ~95.366 | 82 sesiones × ~1.163 tokens (25,5% del presupuesto si es sistemático) |

**Previsión del reviewer para primera noche real**:
- 40 ítems × 3.056 = 122.240 tokens Reader
- Pool Popularizer: 300.000 - 60.000 (reserva) - 122.240 = 117.760 tokens
- Candidatos esperados: 117.760 ÷ 4.560 ≈ 26 (sin reintento)
- Editor (26 candidatos estimados): 4.000 + 26×700 = 22.200 tokens
- Gasto lateral (~95.366 tokens) suma un 31,8% adicional invisible
- **Total gasto real esperado**: ~243.000 de 300.000 tokens (81% utilización)

## Versiones y cambios de baseline

- **Prompt versions en uso**:
  - `reader-v2`: abstract en tags `<abstract></abstract>`
  - `popularizer-v2`: con reparador JSON para caracteres de control
  - `editor-v1`: entrada acotada a `title+level_curious`
- **CLI y SDK en T44**:
  - Claude Code CLI: 2.1.274
  - Agent SDK: 0.2.153
  - Cambios entre versiones invalidan comparaciones directas; anotarlos cada noche

## Noches reales (T60 las rellenará)

| Fecha | Hora inicio | CLI | SDK | Run.status | items_fetched | items_read | items_failed | candidates | findings_published | sum(agent_calls) tokens | % semanal (Settings) | Notas |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-18 | 11:44 | 2.1.274 | 0.2.153 | killed | 0 | 0 | 0 | 0 | 0 | 0 | — | Intento fuera de ventana (6h 59m después del cierre de las 04:45). `deadline_s = 0`, `stop_reason = outside_window`. **La ingesta sí se ejecutó** —hizo su petición HTTP a arXiv, de ahí el 406— y lo que `hard_stop` impidió fue autorizar cualquier llamada a Claude: cero tokens gastados. Validación de control de gasto: funcionó exactamente como se diseñó. `elapsed_s = 0.307`. Nota: arXiv devolvió 406 en esa ejecución (transitorio, después reproducible con 200). |
| 2026-09-18 | 12:34 | 2.1.274 | 0.2.153 | completed | 0 | 39 | 1 | 14 | 10 | 246.608 | — | **Primera noche completa real.** Ejecutada fuera de ventana nominal (00:00–04:45) con `hard_stop` ampliado a mano a 23:59 para validar el circuito sin esperar a medianoche; ventana ya revertida y tests verdes. Ingesta: 34 ítems traídos de arXiv, todos duplicados de ejecución anterior, `items_fetched = 0` en `Run`. **39 ítems leídos por Reader, 1 fallido** tras 2 intentos de validación JSON (`invalid_output`). **14 candidatos** con `interest_score >= 4` de 39 leídos: **35,9%**. Popularizer: 15 llamadas para 14 candidatos, tasa de reintento 7,1%. Editor: 1 intento, 14 candidatos, publicó 10, descartó 4 con confidence 0,60–0,95 y motivos pertinentes. **Total: 246.608 de 300.000 tokens (82,2%).** Desglose: Reader 41 llamadas 153.753 (media 3.750), Popularizer 15 llamadas 80.965 (media 5.398), Editor 1 llamada 11.890. `elapsed_s = 613,7` (10 min 14 s). **Dos hallazgos de calibración decisivos registrados abajo.** |

## Observaciones de calibración

### Hallazgo 1: Margen mínimo en pool compartido Reader+Popularizer (crítico para T60)

Pool teórico = 300.000 - 60.000 (reserva Editor) = 240.000 tokens.
Consumo real: Reader 153.753 + Popularizer 80.965 = **234.718 tokens (97,8%)**.
**Quedaban 5.282 tokens**, pero la media del Popularizer es 5.398 tokens/candidato.

**Conclusión**: Un candidato más y la fase B se habría quedado sin presupuesto. **No es un fallo** —el diseño funcionó, Editor recibió su reserva y la noche cerró `completed`—, pero el margen fue mínimo. **Implicación para T60**: antes de subir `max_items_per_night` (ahora 40) o esperar suba la tasa de candidatos, hay que monitorizar que el pool compartido no se agote. Los 14 candidatos observados son el 35,9% de 39 leídos; si en noches sucesivas la tasa sube a 50%+, el margen se anula.

### Hallazgo 2: Estimaciones vs reales, reparametrización necesaria (para T60)

Con dos observaciones del Editor ya se puede separar parte fija de variable:
- T43 humo: N=3 → 3.381 tokens
- T44 noche: N=14 → 11.890 tokens

**Recta observada**: ~774 tokens/candidato + ~1.060 fija.
**Configuración estimada**: 4.000 fija + 700/candidato = 4.700 + 700×N.
**Punto de cruce**: ambas rectas se cruzan en N ≈ 40 (exactamente `max_items_per_night`).

**Margen hoy**: 11.890 real vs 13.800 estimado para N=14 (ratio 0,86×, es decir, real es 1,16× más pequeño que estimado).
**Peor caso posible**: en N=40 ambas rectas coinciden, margen → 1.

**Recomendación para T60** (no cambiar ahora): subir `editor_tokens_per_candidate` a ~850 y bajar `editor_base_tokens` (está sobredimensionado). Pero también observar: una noche con solo 14 candidatos es observación débil si la tasa media es 35,9%. Esperar segundo punto con N distinto antes de hacer cambios en configuración.

### Tabla: Consumo observado vs estimado

| Agente | Observado (real) | Estimado (config) | Margen |
|---|---|---|---|
| Reader | 3.750 tokens/ítem (media) | 6.000 | 1,6× |
| Popularizer | 5.398 tokens/candidato (media) | 7.000 | 1,3× |
| Editor | 11.890 para N=14 (1 intento) | 13.800 para N=14 | 1,16× |

El modelo es seguro porque **cada intento se autoriza por separado** contra presupuesto. El Popularizer que reintentó (N=1) gastó 11.110 en dos pasadas, ambas dentro de presupuesto autorizado por llamada. El Reader fallido (N=1) gastó 8.094 en dos intentos de validación, ambas contabilizadas.

### Comparación con línea base (pre-T60, humos reales del 2026-09-17)

| Métrica | Humo único (T41–T43) | Noche completa (2026-09-18) | Cambio |
|---|---|---|---|
| Reader tokens/ítem | 3.056 (T41) | 3.750 (media de 41 llamadas) | +244 (+8,0%) |
| Popularizer tokens/candidato | 4.560 (T42, 1 intento, N=1) | 5.398 (media de 15 llamadas, N=14) | +838 (+18,4%) |
| Tasa de reintento Popularizer | 0% (T42 especial, 1 solo humo) | 7,1% (1 de 14) | +7,1 pp |
| Fallida Reader | 0% (T41 sin fallos) | 2,5% (1 de 40) | +2,5 pp |

**Interpretación**: Las medias de un solo humo se quedaron cortas frente a una noche entera con abstracts reales variados. Gasto lateral del CLI (Haiku) sigue sin correlacionar limpiamente con tamaño de prompt; mismo CLI/SDK (2.1.274/0.2.153) gastó 929, 1.163, 1.240 Haiku en tres llamadas sucesivas de T41–T43. La noche completa no permite aislar CLI overhead de otros factores. **Para T60**: registrar versión CLI/SDK cada noche, porque cambios entre versiones invalidan comparaciones.

### Nota sobre prompt versions y no comparabilidad

- `reader-v2`: abstract en tags `<abstract></abstract>`
- `popularizer-v2`: con reparador JSON para caracteres de control
- `editor-v1`: entrada acotada a `title+level_curious`

Estos cambios rompen comparabilidad con datos previos de línea base. Es exactamente para lo que existe el campo `prompt_version` en `AgentCall`. T60 debe anotar este cambio de baselines al comparar gasto, tasas de reintento, etc.
