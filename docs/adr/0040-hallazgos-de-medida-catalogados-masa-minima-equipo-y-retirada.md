# ADR 0040 · Hallazgos de medida: planetas catalogados, masa verdadera, confirmación entre equipos y retirada

Fecha: 2026-11-03 · Estado: aceptado · Tarea: T100 · Modifica: ADR 0020 §4 · Precisa: ADR 0023 §2 · Relacionados: ADR 0015, 0017, 0039

## Contexto

ADR 0039 cerró la fase 2 y registró tres hallazgos de medida erróneos ya publicados:

- **M12** (`ab6940a9-5618-4192-ad1f-17db2c02d52f`): TOI-2211 c. Una masa mínima `m sin i` de un candidato sin designación se guardó como masa y se publicó como `primera_medida`.
- **M14** (`53276767-4ce1-4940-8a0a-002d1cfe1eb8`): TIC 402710280 b. Un candidato a planeta se presentó como planeta.
- **M15** (`30365a4f-dd57-4f52-880b-99c35ca3d3a2`): TOI-2158 b. La «confirmación independiente» citaba a Knudstrup et al. 2022 frente a Knudstrup et al. 2026: mismo primer autor.

En la muestra de ADR 0039 §3, de 14 `primera_medida` solo 3 eran de planetas catalogados sin valor comparable. Los otros 11 eran de planetas ausentes del archivo, que repetían el paper de descubrimiento. De las 3 `confirmacion_independiente`, ninguna resultó útil.

Antes de T100, `ADR 0017` fijaba `published_at` como única fuente de verdad de lo publicado, y no había forma de dejar de mostrar un hallazgo sin borrarlo.

## Decisión

Decisión del autor (T100, 2026-11-03), con las recomendaciones del plan D1–D10. Cuatro puntos:

1. **`primera_medida` solo para planetas catalogados sin valor comparable** (`no_comparable_solution`). Un planeta ausente del archivo (`absent`) ya no genera. `ArchiveStatus.ABSENT` y su plantilla se conservan para leer JSON ya guardado; `first_measurement_from` lanza con `absent`. Modifica ADR 0020 §4.

2. **La masa mínima no es masa.** `domain/minimum_mass.py::states_minimum_mass` decide sobre la cita literal de la medida y el contexto a su izquierda, dentro de la misma cláusula:
   - Contexto de hasta 80 caracteres. Corta en `. `, `; ` y `, `, salvo coma seguida de cifra, signo o `$` con cifra.
   - Normaliza TeX antes de comparar (`_ ^ \, \; \! \  ~` a espacio; quita `$ \ { }`).
   - Reconoce `sin i`, `sin(i)`, `msini` (con subíndice opcional), `minimum mass` / `minimum-mass` / `minimum masses` y `projected mass`.
   - Solo aplica a masa.

   Se aplica en tres puntos: el filtro del Reader (`DiscardReason.MINIMUM_MASS`, sexta comprobación, sin reintento), la generación de hallazgos de medida (`skipped_minimum_mass`) y la selección del redactor (`catalog_tension_skip_reason`, motivo `minimum_mass` tras `not_evaluated`). La regla va en Python, no en el prompt: `reader-v3` no cambia y no hay tokens nuevos.

3. **`confirmacion_independiente` exige un equipo distinto.** Además de la referencia independiente de ADR 0023 §2, el primer autor de la referencia no puede figurar entre los autores del paper (`domain/team.py`: `reference_first_author` y `shares_first_author`).
   - Autores del paper: `Item.authors` (migración `b7e3a1c9d482`). Vienen de `<authors>` en arXivRaw (OAI) y de `<author><name>` en Atom, unidos con «, ». Ausentes o vacíos, `None`.
   - Estados de `ConfirmationTeamCheck`: `different_team` (única que confirma), `same_team`, `unknown_team` (faltan autores o la referencia no tiene forma reconocible: no hay confirmación) y `not_applicable`.
   - Los ítems anteriores a T100 no reciben autores (D4, sin relleno). No generan confirmaciones hasta que salgan de la ventana de 30 días.
   - Contadores `skipped_same_team` y `skipped_unknown_team` en el informe, en el log y en `run-night --dry-run`.
   - Plantilla técnica: «el primer autor de la referencia (<apellido>) no figura entre los autores del artículo; no se comprueba el resto del equipo ni el método».
   - `editor-v4` no cambia (D8). Modifica ADR 0020 §4 y precisa ADR 0023 §2.

4. **Retirada de hallazgos publicados.**
   - `Finding.retract(at, note)` solo actúa sobre un hallazgo publicado y no retirado. La nota no puede estar vacía. No toca `published_at` ni `Item.status`.
   - Columnas `findings.retracted_at` y `findings.retraction_note`, con tres CHECK (`ck_findings_retraction_together`, `ck_findings_retraction_only_published`, `ck_findings_retraction_note_not_blank`).
   - El feed (`published_page`, `count_published` y `ListPublishedFindings` como segunda barrera) excluye los retirados.
   - El detalle (`get_published`, `GetPublishedFinding`) sirve el hallazgo con `retracted_at` y `retraction_note` en la API, por lista blanca y solo en el detalle. La web lo mostrará con aviso en T100.w.
   - `FindingRepository.get(id)` (nuevo) y `save_retraction`.
   - CLI `nocturna retract-finding <finding_id> --note "<texto>" [--dry-run]`. Sale con 1 si el hallazgo no existe, no está publicado, ya está retirado o la nota está vacía. No usa LLM.
   - Retirar no regenera ni re-redacta nada.
   - Textos aprobados (D6):
     - M12: «Retirado: la cifra es una masa mínima (m sin i) de un candidato sin designación; no es la masa de un planeta TOI-2211 c.»
     - M14: «Retirado: TIC 402710280 b es un candidato a planeta, no un planeta confirmado, y no está en el NASA Exoplanet Archive.»
     - M15: «Retirado: la referencia (Knudstrup et al. 2022) es del mismo primer autor que el artículo; no es una confirmación independiente.»

«Publicado» sigue siendo `Finding.published_at` (ADR 0017). Un hallazgo retirado sigue publicado en ese sentido; lo que cambia es la visibilidad del feed y del detalle.

## Consecuencias

- **Migración** `b7e3a1c9d482` sobre `a4d9e1b7c352`: añade `items.authors`, `findings.retracted_at`, `findings.retraction_note` y los tres CHECK. Pendiente de aplicar a la base real tras el merge, fuera de 00:00–04:45. Su downgrade se niega (`RuntimeError`) si hay retirados, y borra `items.authors`: los autores solo se recuperan re-cosechando.
- **Gasto.** Ningún camino nuevo llama a un agente. `budget.py`, prompts, estimaciones, reservas y `pipeline.toml` no cambian. Las reglas solo pueden bajar candidatos al Editor y llamadas al redactor. Revisión de gasto aprobada.
- **Hallazgos ya publicados.** M12, M14 y M15 siguen visibles hasta que T100.w esté cerrada. Retirarlos antes dejaría su ficha sin aviso. Tras T100.w, el autor ejecuta `retract-finding` con los textos de D6 fuera de 00:00–04:45. Se retiran, no se borran.
- **Con T100 aplicado, el corte de `max_candidates_per_night = 0`** (OD T78) deja de ser necesario para los tres patrones de error detectados. Sigue siendo una decisión del autor.
- **Consulta de solo lectura, 2026-11-03:**
  - De las 24 medidas de masa `reader-v3` guardadas, la regla de masa mínima marca solo una: TOI-2211 c (2610.08922).
  - De las evaluaciones sin `Finding`, la única confirmación elegible hoy (TOI-6662 b, 2610.42009, referencia Collier et al. 2026) deja de serlo: su ítem no tiene autores (D4).

## Limitaciones conocidas

También en `docs/TECHNICAL_DEBT.md`.

- **Independencia.** Solo se compara el primer autor de la referencia, porque el archivo no da coautores. Un equipo que rota el primer autor no se detecta. La transliteración alemana `ue/oe/ae` no está cubierta: «Müller» frente a «Mueller» da equipo distinto, dirección arriesgada. `\dj` tampoco. Apellidos cortos y afiliaciones dentro de `<authors>` de arXivRaw pueden marcar «mismo equipo» de más (dirección conservadora).
- **Masa mínima.**
  - La excepción «coma seguida de cifra» puede unir dos cláusulas. «M sin i = 30 M_E for c, 2 transits of b yield a mass of 5.0 M_E» da masa mínima (conservador).
  - «RV M sin i combinado con astrometría da masa verdadera» se descarta como mínima (conservador: se pierden masas verdaderas de RV con astrometría).
  - `sin i` sin `\b` delante: «Wisconsin I» casaría.
  - La lista de comandos TeX es cerrada.
- **Candidatos sin designación.** Por decisión del plan, se excluyen porque no están en el archivo (punto 1), no con una regla propia: la tabla de soluciones del archivo solo trae planetas confirmados, así que un candidato no llega a ningún hallazgo de medida ni a `catalog_tension`. M12 y M14 quedan fuera así. Queda un caso residual: si el Reader atribuyera la medida de un candidato a la designación de un planeta confirmado que sí está en el archivo, la regla no lo detectaría.
- **Ítems anteriores a T100.** No tienen autores y no generan confirmaciones. Ese caso se agota con la ventana de 30 días.

## Alternativas descartadas

- **reader-v4 con campos `mass_kind` e `is_candidate`.** Cambia el prompt y el contrato del Reader, con su estimación de gasto, para una distinción que se resuelve en Python sin tokens.
- **Regex sobre la palabra «candidate».** Se descartó en favor de la regla de masa mínima sobre la cita literal y el contexto.
- **Borrar `published_at` o los hallazgos.** Pierde el rastro de lo publicado y de su corrección.
- **Nota de corrección sin retirada.** La nota se queda en la ficha y el hallazgo sigue en el feed; no excluye el error de la vista general.
- **Rellenar autores de ítems antiguos** (ADS o re-ingesta). ADS exige token (ADR 0023). Re-ingerir ítems fuera de la ventana no era el alcance de T100; el caso que lo habría necesitado es TOI-6662 b (ver Consecuencias).
