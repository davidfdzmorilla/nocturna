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
|  |  |  |  |  |  |  |  |  |  |  |  |  |

## Observaciones de calibración

(Se rellenará durante T60 con hallazgos de tasa de reintento, orden de candidatos, calidad editorial, etc.)
