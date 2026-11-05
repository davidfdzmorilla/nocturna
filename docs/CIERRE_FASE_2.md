# Cierre de fase 2 · documento de trabajo (T78)

Material para la decisión final de la fase 2 (ADR 0039). El criterio de cierre está en [ADR 0021](adr/0021-criterio-de-cierre-de-fase-2.md) y se cumple.

- Corte: noche del 2026-11-03 (D1).
- Datos: consultas de solo lectura (`SET default_transaction_read_only = on`) sobre la base real, ejecutadas el 2026-11-03 entre las 09:30 y las 10:00. Fechas en `Europe/Madrid`.
- Las consultas están al final, en «Consultas de referencia».
- §1 y §2 los prepara el orquestador. Los veredictos de §2 y las marcas de §3 los hizo Claude por encargo del autor (2026-11-03); el autor puede cambiarlos.

## §1 · Registro por noche

Una fila por `Run`. Las relecturas de T82 (`notes = 'reread'`) se marcan «rel.» y no cuentan como noche. Entre el 2026-10-11 y el 2026-10-13 no hubo `Run`.

### Columnas

| Columna | Qué cuenta |
|---|---|
| EP | ítems astro-ph.EP leídos (Reader `ok`) |
| v3 | ítems leídos con `reader-v3` |
| med | medidas guardadas tras los filtros de T71.c |
| útil | medidas utilizables: `this_work`, sin límite y con errores |
| grp / emp / ev / ≥3σ | grupos de `tension_evaluation` (lectura, planeta, parámetro): total, emparejados con el archivo, `evaluated` y `evaluated` con σ ≥ 3,0 |
| v3+ | tokens de más del Reader v3, aproximados (ver limitaciones) |
| red. | llamadas y tokens del redactor |
| Ed. | llamadas y tokens del Editor |
| cand/pub | candidatos y publicados, todos los tipos |
| ten c/p | tensiones (`catalog_tension` + `solution_tension`): candidatas / publicadas |
| med c/p | hallazgos de medida (`primera_medida` + `confirmacion_independiente`): candidatos / publicados |

### Tabla

| Noche | Estado | Tokens | EP | v3 | med | útil | grp | emp | ev | ≥3σ | v3+ | red. | Ed. | cand/pub | ten c/p | med c/p |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|
| 09-30 | completed | 197.570 | 11 | 11 | 0 | 0 | | | | | 23.696 | | 1 · 8.919 | 9/9 | 0/0 | 0/0 |
| 10-01 | completed | 212.272 | 14 | 14 | 0 | 0 | | | | | 25.131 | | 1 · 10.093 | 10/9 | 0/0 | 0/0 |
| 10-02 | completed | 246.269 | 19 | 17 | 9 | 8 | 5 | 2 | 1 | 0 | 34.167 | | 1 · 14.368 | 17/11 | 0/0 | 0/0 |
| 10-03 | completed | 208.771 | 8 | 1 | 0 | 0 | | | | | 1.387 | | 1 · 14.307 | 16/10 | 0/0 | 0/0 |
| 10-04 | completed | 188.530 | 6 | 0 | | | | | | | | | 1 · 10.807 | 12/9 | 0/0 | 0/0 |
| 10-05 | completed | 186.210 | 7 | 0 | | | | | | | | | 1 · 12.437 | 13/10 | 0/0 | 0/0 |
| 10-06 | partial | 137.854 | 7 | 2 | 3 | 3 | 3 | 3 | 1 | 1 | 4.474 | | 1 · 9.420 | 10/10 | 0/0 | 3/3 |
| 10-06 rel. ×5 | completed | 33.551 | 5 | 5 | 21 | 18 | 13 | 12 | 5 | 2 | 15.581 | | | | | |
| 10-07 | completed | 180.090 | 12 | 3 | 0 | 0 | | | | | 6.444 | | 1 · 13.430 | 15/15 | 0/0 | 5/5 |
| 10-08 | completed | 233.104 | 11 | 5 | 4 | 4 | 4 | 3 | 1 | 0 | 12.674 | | 1 · 19.944 | 21/19 | 0/0 | 4/4 |
| 10-09 | completed | 220.602 | 10 | 4 | 3 | 3 | 3 | 2 | 1 | 0 | 8.194 | 2 · 12.862 (`writer-v1`) | 1 · 17.956 | 18/15 | 2/1 | 1/1 |
| 10-10 | completed | 164.768 | 12 | 4 | 0 | 0 | | | | | 7.209 | 1 · 8.720 (`writer-v1`) | 1 · 10.997 | 10/8 | 1/0 | 3/3 |
| 10-14 | completed | 213.240 | 5 | 0 | | | | | | | | | 1 · 15.636 | 15/14 | 0/0 | 0/0 |
| 10-15 | partial | 141.777 | 4 | 0 | | | | | | | | 2 · 13.295 (`writer-archive-v1`) | 1 · 12.218 | 10/10 | 2/2 | 0/0 |
| 10-16 | completed | 191.867 | 16 | 11 | 0 | 0 | | | | | 22.576 | 2 · 11.318 (`writer-archive-v1`) | 1 · 11.988 | 11/10 | 2/2 | 0/0 |
| 10-17 | completed | 214.277 | 14 | 10 | 0 | 0 | | | | | 20.558 | 2 · 13.374 (`writer-archive-v1`) | 2 · 22.704 (1 `invalid_output` + reintento `ok`) | 10/9 | 2/1 | 0/0 |
| 10-18 | completed | 226.016 | 11 | 0 | | | | | | | | 2 · 14.376 (`writer-archive-v1`) | 1 · 17.953 | 17/15 | 2/1 | 0/0 |
| 10-19 | completed | 183.340 | 5 | 0 | | | | | | | | 2 · 13.482 (`writer-archive-v1`) | 1 · 12.502 | 10/6 | 2/0 | 0/0 |
| 10-20 | completed | 184.524 | 7 | 3 | 0 | 0 | | | | | 5.659 | | 1 · 13.006 | 12/11 | 0/0 | 0/0 |
| 10-21 | completed | 208.565 | 10 | 4 | 2 | 2 | 2 | 0 | 0 | 0 | 9.009 | | 1 · 13.104 | 13/10 | 0/0 | 0/0 |
| 10-22 | completed | 213.681 | 15 | 6 | 0 | 0 | | | | | 9.531 | | 1 · 15.710 | 15/11 | 0/0 | 1/1 |
| 10-23 | partial | 175.020 | 5 | 0 | | | | | | | | | 1 · 11.944 | 11/9 | 0/0 | 0/0 |
| 10-24 | partial | 41.719 | 3 | 0 | | | | | | | | | 1 · 5.498 | 2/2 | 0/0 | 0/0 |
| 10-25 | completed | 185.552 | 13 | 11 | 0 | 0 | | | | | 20.733 | | 1 · 9.143 | 7/6 | 0/0 | 0/0 |
| 10-26 | completed | 180.426 | 8 | 0 | | | | | | | | | 1 · 11.028 | 9/9 | 0/0 | 0/0 |
| 10-27 | completed | 214.518 | 9 | 4 | 0 | 0 | | | | | 7.777 | | 1 · 15.117 | 14/12 | 0/0 | 0/0 |
| 10-28 | completed | 204.693 | 10 | 3 | 0 | 0 | | | | | 5.701 | | 1 · 15.079 | 14/13 | 0/0 | 0/0 |
| 10-29 | partial | 0 | 0 | 0 | | | | | | | — | | | | | |
| 10-30 | partial | 0 | 0 | 0 | | | | | | | — | | | | | |
| 10-31 | partial | 212.461 | 5 | 1 | 0 | 0 | | | | | 2.611 | | 1 · 15.683 | 14/12 | 0/0 | 0/0 |
| 11-01 | partial | 183.182 | 7 | 0 | | | | | | | | | 1 · 12.197 | 10/9 | 0/0 | 0/0 |
| 11-02 | completed | 208.400 | 9 | 5 | 0 | 0 | | | | | 9.348 | | 1 · 12.006 | 11/8 | 0/0 | 0/0 |
| 11-03 | completed | 225.475 | 6 | 3 | 1 | 1 | 1 | 1 | 1 | 0 | 6.387 | 2 · 26.425 (`writer-digest-v1`) | 1 · 11.102 | 9/8 | 0/0 | 0/0 |

### Totales del periodo

- **Noches:** 32 (24 `completed`, 8 `partial`), más 5 relecturas.
- **Lecturas v3:** 122 en noches y 5 relecturas.
- **Medidas guardadas:** 22 en noches (21 utilizables) y 21 en relecturas (18 utilizables).
- **Medidas por noche:** desde el 2026-10-10 solo dos noches tienen alguna (10-21 con 2 y 11-03 con 1).
- **Tokens de más del Reader v3:** unos 240.000 en noches, unos 2.000 por lectura v3. Las relecturas añaden 15.581.
- **Redactor:** 15 llamadas y 113.852 tokens, todas `ok`.
  - `writer-v1`: 3 llamadas.
  - `writer-archive-v1`: 10 llamadas.
  - `writer-digest-v1`: 2 llamadas.
- **Editor:** una llamada por noche, salvo el 10-17. Esa noche hubo un `invalid_output` y el único reintento permitido.
- **`tension_evaluation`:** 31 grupos; todos cuelgan de una lectura v3 con su `Run`.
- **Vía del archivo (`solution_tension`):** 99 tensiones calculadas el 2026-10-14 (8 elegibles) y 7 el 2026-10-16 (2 elegibles). Desde entonces no se ha calculado ninguna más, porque el archivo no publica desde el 2026-10-15 (ADR 0021, C1).
- **Publicados no `paper_explained`:**
  - 7 tensiones, todas del 2026-10-09 al 2026-10-18;
  - 17 hallazgos de medida, el último del 2026-10-22.

### Limitaciones

- **Medidas extraídas.** Solo se guardan las que pasan los filtros de T71.c; las descartadas quedan solo en los logs. La columna es «guardadas», no «extraídas» (D4).
- **Emparejadas.** Es una foto de hoy, por grupo y no por medida. `awaiting_reference` pasa a otro estado sin dejar histórico.
- **v3+.** Se calcula como tokens de las llamadas v3 menos el número de llamadas v3 por la media de `reader-v2` de esa noche (o la media global si esa noche no hubo v2). Tiene sesgo, porque v2 incluye astro-ph.GA. En el 10-29 sale negativo (−3.594) por una única llamada fallida con 0 tokens; se muestra «—».
- **Atribución de lecturas a noches.** `readings` no tiene `run_id`; cada lectura se ata a su noche por `agent_calls` (Reader `ok`).

## §2 · Muestreo de atribución

- **Unidad:** el abstract entero, con todas sus medidas, para poder detectar también omisiones.
- **Población:** lecturas `reader-v3` vigentes, sin los 4 abstracts de T71.b (2609.20748, 2609.26894, 2609.30038 y 2609.17025).
- **Estratos (D7):**
  - **S1:** todas las lecturas de las que sale algún hallazgo. 8 lecturas, 28 medidas.
  - **S2:** el resto de lecturas con medidas. Son 2, así que van enteras: 4 medidas.
  - **S3:** 10 lecturas sin medidas pero con `exoplanet_match` cierto, sacadas de un total de 94 por orden de `md5('T78:' || external_id)`. Sirven para buscar omisiones.

Veredictos posibles: correcta / planeta / origen / valor-unidad-límite / omisión. «m sin i» se marca aparte y no cuenta como error (riesgo aceptado en T71.c).

Veredicto propuesto por Claude (D5). El plan pedía que el autor confirmara todo S1, todo lo que no fuera «correcta» y una muestra del resto (columna «Autor»). El 2026-11-03 el autor delegó también esa comprobación en Claude: está en §2.b y la columna «Autor» queda vacía.

### S1 · lecturas con hallazgo (censo)

| Abstract | Medidas | Propuesta | Nota | Autor |
|---|---|---|---|---|
| [2609.35747](https://arxiv.org/abs/2609.35747) TOI-210 b | masa 6,75 ± 1,25 M⊕; radio 2,234 ± 0,074 R⊕ | 2 correctas | El periodo de 9,01 d no tiene error y no se extrae (bien). Las dos señales Kepler de 2,15 y 3,76 d son candidatos sin nombre con masa mínima y no se extraen (bien). | |
| [2609.37597](https://arxiv.org/abs/2609.37597) TOI-6981 b | radio 2,40 ± 0,10 R⊕; periodo 60,8 d sin errores | 2 correctas | El periodo se guarda sin errores y no es utilizable (bien). | |
| [2609.35979](https://arxiv.org/abs/2609.35979) HIP 67522 b y c | b: 25,0 +7,6/−7,8 y 23,1 +15,4/−12,4 M⊕; c: 11,2 ± 1,4, 11,8 +3,0/−2,3 y 9,7 +1,9/−1,6 M⊕ | 5 correctas | Varias soluciones del mismo planeta: circular, excéntrica y excéntrica con prior de JWST, que es la recomendada para c. El Reader las guarda todas sin decir cuál es la preferida. | |
| [2610.02783](https://arxiv.org/abs/2610.02783) HD 65216 | c: periodo 7007 +192/−161 d; b: masa 2,23 +0,96/−0,77 M♃; c: masa 3,32 +1,40/−1,16 M♃ | 3 correctas | Las masas son «verdaderas», pero dependen de un modelo: distribución bimodal de inclinación, sin i ≈ 0,5 y planetas coplanares. Es this_work, así que la etiqueta es correcta; el matiz se pierde en `primera_medida`. | |
| [2610.07133](https://arxiv.org/abs/2610.07133) OGLE-2006-BLG-284 b | masa 165 +40/−33 M⊕ | 1 correcta | Las masas de las dos estrellas no se extraen (bien). | |
| [2609.30915](https://arxiv.org/abs/2609.30915) TOI-5120 b, TOI-5699 b, TOI-2158 b y c | 9 de masa, radio y periodo de b; periodo de TOI-2158 c | 10 correctas | El `M_p sin i` = 1,62 M♃ de TOI-2158 c no se guarda como masa (bien). | |
| [2610.08922](https://arxiv.org/abs/2610.08922) TOI-2211 b y c | b: radio 1,457 ± 0,047 R⊕ y masa 2,69 ± 0,56 M⊕; «c»: masa 8,53 ± 1,04 M⊕ | 2 correctas, **1 m sin i** | **A revisar.** El abstract da `Msin(i) = 8.53 ± 1.04 M⊕` de un «candidato a planeta exterior que no transita», sin la designación «c». El Reader lo guardó como masa de TOI-2211 c y se publicó la `primera_medida` «Primera medida de la masa de TOI-2211 c» (ab6940a9…, confianza 0,70). En el mismo lote, TOI-2158 c sí se filtró. Cae en D8: se registra, no se despublica. | |
| [2610.26056](https://arxiv.org/abs/2610.26056) TIC 402710280 b | radio 3,75 ± 0,22 R⊕; periodo 36,33986 ± 0,00051 d | 2 correctas | Es un **candidato** («sub-Neptune candidate»), no un planeta confirmado. Su `primera_medida` lo presenta como planeta. | |

### S2 · resto con medidas (censo)

| Abstract | Medidas | Propuesta | Nota | Autor |
|---|---|---|---|---|
| [2610.42009](https://arxiv.org/abs/2610.42009) TOI-6662 b | radio 0,94 ± 0,03 R⊕ | 1 correcta | El periodo de 7,05 h no tiene error y no se extrae (bien). | |
| [2610.08065](https://arxiv.org/abs/2610.08065) HD 715 b | periodo 56,40 +0,19/−0,23 d; masa 0,635 ± 0,056 M♃; radio 1,07 ± 0,04 R♃ | 3 correctas | La cita del radio arrastra texto de más, pero sostiene el valor. | |

### S3 · sin medidas, `exoplanet_match` cierto (10 de 94)

| Abstract | Propuesta | Nota | Autor |
|---|---|---|---|
| [2610.28485](https://arxiv.org/abs/2610.28485) Solar Optical Clock | correcta (sin medidas) | Instrumentación. | |
| [2610.13200](https://arxiv.org/abs/2610.13200) HD 219134 | correcta | Solo parámetros estelares. | |
| [2609.37160](https://arxiv.org/abs/2609.37160) compañeros astrométricos | correcta | Estadística sin valores por planeta. | |
| [2610.33643](https://arxiv.org/abs/2610.33643) CH4→CO en gigantes gaseosos | correcta | Modelos atmosféricos. | |
| [2609.32893](https://arxiv.org/abs/2609.32893) 61 Cygni | **dudosa** | Compañero **candidato sin designación**: masa ≈ 8 ± 3 M♃ (this_work) y radio 1,02 ± 0,02 R♃ sacado de modelos evolutivos. No extraerlo es coherente con exigir nombre de planeta, pero es la única omisión posible de la muestra. | |
| [2610.22233](https://arxiv.org/abs/2610.22233) ALMA2040 Venus y Marte | correcta | Sistema solar. | |
| [2610.32815](https://arxiv.org/abs/2610.32815) He I en V1298 Tau b | correcta | Atmósfera, sin masa, radio ni periodo. | |
| [2610.22271](https://arxiv.org/abs/2610.22271) WASP-94A b | correcta | Temperatura y abundancias, sin parámetros del alcance. | |
| [2610.23500](https://arxiv.org/abs/2610.23500) taller ACSIOM | correcta | Sin datos. | |
| [2610.19328](https://arxiv.org/abs/2610.19328) ExoEcho TTV | correcta | Da Ṗ y amplitud de TTV, no el periodo. | |

### Resumen propuesto

- **Medidas revisadas:** 32.
  - 31 correctas en planeta, parámetro, unidad, valor y errores, límite, origen y cita.
  - 1 `m sin i` guardada como masa (TOI-2211 c), que acabó en una `primera_medida` publicada.
- **Omisiones:** ninguna clara en S1 ni S2. En S3, 1 dudosa de 10 (61 Cygni, candidato sin nombre).
- **Matices que no son errores de atribución,** pero afectan a lo que dice el hallazgo publicado:
  - masas dependientes de modelo (HD 65216);
  - candidatos presentados como planetas (TIC 402710280 b y TOI-2211 c);
  - varias soluciones del mismo planeta (HIP 67522).
- **Cobertura de S3:** 94 lecturas tienen `exoplanet_match` cierto y ninguna medida. La muestra sugiere que la mayoría no traen medidas (instrumentación, atmósferas, sistema solar, estadística), no que el Reader las omita.

### §2.b · Comprobaciones contra el archivo (2026-11-03)

Hechas por Claude por encargo del autor. Se contrastaron los veredictos dudosos y todas las fichas con `archive_solution` y con el `first_measurement` / `independent_confirmation` guardado en cada `Finding`.

- **Veredictos de §2 confirmados.** TOI-2211 c es un error que se publicó: m sin i de un candidato sin nombre. El texto técnico del hallazgo dice «planeta no encontrado (servicio de alias: System Not Found)»; el título dice «Primera medida de la masa de TOI-2211 c». TIC 402710280 b tampoco está en el archivo (`absent`) y el abstract lo llama candidato. 61 Cygni queda como dudosa sin consecuencia: nada se publicó.
- **`primera_medida` mide sobre todo planetas que el archivo aún no tiene.**
  - 11 de 14 tienen `archive_status = absent`: el planeta no está en el archivo. El hallazgo repite el paper de descubrimiento con una cifra, y los dos errores están en este grupo.
  - Solo 3 son primeras medidas de un planeta ya catalogado (`no_comparable_solution`): HD 65216 b y c (primera masa verdadera, antes solo m sin i) y TOI-2211 b (en el archivo como TOI-2211.01, sin masa).
- **`confirmacion_independiente` no exige otro equipo.** TOI-2158 b se «confirma» con Knudstrup 2022 a partir de Knudstrup 2026, el mismo primer autor. Cumple la regla de T89 (otro paper), pero la etiqueta «independiente» engaña.
- **Tensiones del archivo.**
  - Las 6 publicadas tienen detrás un cambio real entre soluciones guardadas (§3).
  - Las σ grandes (289 en HD 114082 b) salen de errores muy pequeños de la solución nueva; el cambio de fondo es real (periodo ×2,05).
  - En HD 202206 b y c la discrepancia está entre dos masas verdaderas (`Mass`), no entre m sin i y masa.
- **Acuerdo con el Editor.** En 11 de las 13 tensiones la valoración coincide con su decisión. Discrepa en dos: T2 (V1298 Tau b, descartada por el Editor y marcada interesante) y T8 (duplicado de T1, publicada).

### Resultado de la valoración

| Grupo | Interesantes / total | Problemas |
|---|---|---|
| `catalog_tension` (vía arXiv) | 2 / 3 (T1, T2) | — |
| `solution_tension` (vía archivo) | 5 / 10 (T4, T5, T6, T7, T9); 5 / 6 entre las publicadas | T8 es un duplicado |
| `primera_medida` | 3 / 14 (M9, M10, M13) | M12 error, M14 engañoso |
| `confirmacion_independiente` | 0 / 3 | M15 no independiente |

Resultados de tensión distintos e interesantes: 7, todos entre el 2026-10-09 y el 2026-10-18 y en 5 sistemas (HD 65216, V1298 Tau, HD 114082, HD 202206, TOI-4504). Las tensiones son lo valioso de la fase y el ruido viene de los hallazgos de medida, pero el ritmo depende de que el archivo publique: desde el 2026-10-15 no publica.

## §3 · Fichas de valoración

Marcas hechas por Claude el 2026-11-03 por encargo del autor («haz tú las comprobaciones»), tras contrastar cada caso con las soluciones del archivo guardadas en la base (§2.b). El autor puede cambiar cualquiera. Criterio: «sí» si el hallazgo aporta algo que no está ya en el paper o en el archivo y es correcto (revisión importante de un valor conocido, cambio de naturaleza del objeto o primera medida de un planeta ya catalogado); «no» si es rutina, duplicado o engañoso. El enlace a la web funciona con la API y la web locales levantadas; los descartados por el Editor no tienen página (la API da 404), así que su texto de nivel «curioso» va debajo de la tabla. El motivo del Editor no se guarda (OD T43).

### Tensiones (13)

| # | Tipo | Planeta · parámetro | σ | Publicado (conf.) | Fuentes | Web | ¿Interesante? | Comentario |
|---|---|---|---:|---|---|---|---|---|
| T1 | `catalog_tension` | HD 65216 c · periodo | 10,09 | 2026-10-09 (0,80) | [arXiv 2610.02783](https://arxiv.org/abs/2610.02783) | [ficha](http://localhost:3000/hallazgo/a963a5c4-8108-458a-8099-31d5643e8e9e) | sí | Revisión real del periodo: 7007 d frente a 5370 ± 20 d de Wittenmyer 2019 (+30 %). |
| T2 | `catalog_tension` | V1298 Tau b · masa | 3,37 | descartado (10-09) | [arXiv 2609.30038](https://arxiv.org/abs/2609.30038) | — | sí | Factor ~13 en masa entre soluciones del archivo (0,52–0,78 M♃ frente a 13,1 M⊕ de Livingston 2026); el paper coincide con Suárez Mascareño 2022. El Editor la descartó; la valoración discrepa. |
| T3 | `catalog_tension` | TOI-2158 b · radio | 3,18 | descartado (10-10) | [arXiv 2609.30915](https://arxiv.org/abs/2609.30915) | — | no | Mismo equipo (Knudstrup 2026 frente a 2022), 6 % de radio: refinamiento. |
| T4 | `solution_tension` | HD 114082 b · periodo | 289,50 | 2026-10-15 (0,60) | del Burgo et al. 2026 frente a Zakhozhay et al. 2022 | [ficha](http://localhost:3000/hallazgo/b2b34e10-7c38-4c64-bb2c-902b3e1da4d7) | sí | El periodo pasa de 109,75 d (RV, Zakhozhay 2022) a 225,55 d (tránsitos, del Burgo 2026) y la masa de ~8 a ~1,6 M♃. Razón 2,055: no es un alias exacto. El 289σ está inflado por el error de 0,0004 d, pero el cambio es real. |
| T5 | `solution_tension` | HD 202206 b · masa | 10,84 | 2026-10-15 (0,60) | Cao et al. 2026 frente a Benedict & Harrison 2017 | [ficha](http://localhost:3000/hallazgo/0df897bb-7ff4-4721-9935-d1118bab32ab) | sí | Cambia la naturaleza del objeto: 21,5 M♃ (Cao 2026, enana marrón) frente a 93,6 M♃ (Benedict & Harrison 2017, estrella de baja masa). Las dos son masas verdaderas (`Mass`). |
| T6 | `solution_tension` | HD 202206 c · masa | 8,20 | 2026-10-16 (0,70) | Cao et al. 2026 frente a Benedict & Harrison 2017 | [ficha](http://localhost:3000/hallazgo/f069f738-4d9f-475c-b636-7c631b5f57d1) | sí | 3,1 M♃ (Cao 2026) frente a 17,9 M♃ (Benedict & Harrison 2017): planeta frente a enana marrón. Masas verdaderas las dos. |
| T7 | `solution_tension` | TOI-4504 c · masa | 4,84 | 2026-10-16 (0,55) | Almenara et al. 2026 frente a Vítková et al. 2025 | [ficha](http://localhost:3000/hallazgo/d6794f28-c29e-42ab-800d-53ee40a10a12) | sí (moderado) | Reanálisis TTV: 2,58 frente a 3,77 M♃. Diferencia del 30 % típica de modelos TTV; va con T9. |
| T8 | `solution_tension` | HD 65216 c · periodo | 10,09 | 2026-10-17 (0,65) | Wallace et al. 2026 frente a Wittenmyer et al. 2019; [arXiv 2610.02783](https://arxiv.org/abs/2610.02783) | [ficha](http://localhost:3000/hallazgo/30244744-5e7f-4b10-bc03-04609d27c4f2) | no (duplicado de T1) | Mismo resultado que T1 por la vía del archivo (ADR 0034). |
| T9 | `solution_tension` | TOI-4504 d · masa | 4,20 | 2026-10-18 (0,75) | Almenara et al. 2026 frente a Vítková et al. 2025 | [ficha](http://localhost:3000/hallazgo/02316704-e746-4967-9631-8d10b1470f66) | sí (moderado) | Mismo reanálisis que T7: 2,02 frente a 1,42 M♃. |
| T10 | `solution_tension` | K2-312 c · periodo | 4,62 | descartado (10-17) | Naponiello et al. 2026 frente a Bonomo et al. 2023; [arXiv 2607.19325](https://arxiv.org/abs/2607.19325) | — | no | Refinamiento de una órbita larga (921 ± 11 d → 871,32 ± 0,13 d) con más baseline; rutina de RV. |
| T11 | `solution_tension` | K2-223 c · periodo | 3,92 | descartado (10-18) | Jankowski et al. 2026 frente a Adams et al. 2021; [arXiv 2609.01721](https://arxiv.org/abs/2609.01721) | — | no | Diferencia de 0,0014 d; el valor nuevo coincide con dos de 2018 y la discrepante es la de 2021. |
| T12 | `solution_tension` | TOI-2158 b · radio | 3,18 | descartado (10-19) | Knudstrup et al. 2026 frente a Knudstrup et al. 2022; [arXiv 2609.30915](https://arxiv.org/abs/2609.30915) | — | no | Mismo caso que T3 por la vía del archivo; mismo equipo. |
| T13 | `solution_tension` | HD 202206 c · periodo | 3,46 | descartado (10-19) | Cao et al. 2026 frente a Benedict & Harrison 2017 | — | no | 3 % en periodo, 3,5σ; marginal. Además el texto tiene una frase rota («unos pocos días de más del 3 por ciento»). |

T1 y T8 son el mismo resultado por las dos vías (ADR 0034).

**Texto «curioso» de los descartados:**

- **T2 · V1298 Tau b.** «Un artículo reciente sobre el planeta V1298 Tau b da varias estimaciones de su masa, todas entre 0,52 y 0,78 veces la masa de Júpiter. Una de las soluciones previas guardadas en el archivo de exoplanetas, de Livingston et al. 2026, apunta a una masa mucho menor, equivalente a unas 13,1 masas de la Tierra. La diferencia entre estos nuevos valores y esa solución previa es lo bastante grande como para merecer una revisión, aunque no sabemos todavía a qué se debe. En cambio, otra solución previa, de Suárez Mascareño et al. 2022, sí coincide bien con los nuevos valores. Ninguno de los dos conjuntos de cifras debe tomarse como el correcto.»
- **T3 · TOI-2158 b.** «Un artículo reciente sobre el planeta TOI-2158 b mide su radio en 0.899 veces el de Júpiter. Al comparar esa cifra con un valor que ya existía en el archivo de exoplanetas de la NASA, atribuido a Knudstrup et al. 2022, aparece una diferencia que conviene revisar, no un error confirmado ni un descubrimiento. Esa misma medida nueva sí coincide, dentro de los márgenes de incertidumbre, con otro valor anterior registrado por ExoFOP. Todavía no sabemos a qué se debe la diferencia con el otro valor; solo que existe y que merece más atención antes de sacar conclusiones.»
- **T10 · K2-312 c.** «El periodo orbital del planeta K2-312 c aparece con dos valores distintos en el archivo de exoplanetas. Uno es de unos 871,32 días, que es la solución que se usa por defecto, publicada por Naponiello y colaboradores en 2026. El otro es de unos 921,2 días, la solución más reciente de otro trabajo, publicada por Bonomo y colaboradores en 2023. Esta diferencia es algo calculado que merece revisarse, no un hecho confirmado ni un error del archivo. No sabemos cuál de los dos valores es el acertado ni por qué difieren.»
- **T11 · K2-223 c.** «K2-223 c es un planeta cuyo periodo orbital aparece calculado de dos maneras distintas en el archivo de exoplanetas. Un trabajo reciente, de Jankowski et al. 2026, da un periodo de 4,56395 días, mientras que un trabajo anterior, de Adams et al. 2021, había calculado 4,562546 días. La diferencia entre ambos es muy pequeña, pero algo mayor de lo esperado si solo se tuvieran en cuenta los márgenes de error publicados, así que se marca como una diferencia que merece revisarse, no como un error confirmado ni un descubrimiento. Por otro lado, el nuevo valor sí coincide, dentro de sus márgenes, con otras dos mediciones anteriores, ambas de 2018.»
- **T12 · TOI-2158 b.** «Para el planeta TOI-2158 b hay dos medidas distintas de su tamaño (su radio) registradas en el archivo de exoplanetas. Una, que acaba de entrar en octubre de 2026, da un radio de unos 10,08 veces el de la Tierra. Otra, de 2022, daba un radio algo mayor, unas 10,76 veces el de la Tierra. La diferencia entre ambas es mayor de lo que cabría esperar solo por el margen de error de cada medida, así que se trata de una diferencia entre dos soluciones del archivo que merece revisarse, no de un error confirmado ni de un descubrimiento. No sabemos cuál de los dos valores se acerca más a la realidad.»
- **T13 · HD 202206 c.** «Para el planeta HD 202206 c hay dos cálculos distintos de cuánto dura su órbita (su periodo), y no coinciden del todo. Uno, más reciente, da 1299,22 días. Otro, de un trabajo anterior, da 1260,0 días. La diferencia entre ambos es de unos pocos días de más del 3 por ciento. Esto no significa que uno esté equivocado y el otro bien: es simplemente una diferencia entre dos soluciones guardadas en el mismo archivo de datos, que convendría revisar con más calma antes de sacar conclusiones.»

### Hallazgos de medida (17, todos publicados)

| # | Tipo | Planeta · parámetro | Valor (título) | Publicado (conf.) | arXiv | Web | ¿Interesante? | Comentario |
|---|---|---|---|---|---|---|---|---|
| M1 | `primera_medida` | TOI-210 b · masa | 6,75 ± 1,25 M⊕ | 2026-10-06 (0,62) | [2609.35747](https://arxiv.org/abs/2609.35747) | [ficha](http://localhost:3000/hallazgo/cbc1972c-1c6d-482d-9427-6dbe9cca2b82) | no | Planeta ausente del archivo: repite el paper de descubrimiento. |
| M2 | `primera_medida` | TOI-6981 b · radio | 2,4 ± 0,1 R⊕ | 2026-10-06 (0,50) | [2609.37597](https://arxiv.org/abs/2609.37597) | [ficha](http://localhost:3000/hallazgo/f7497068-fb86-4089-a94c-1fa6b29e486b) | no | Ausente del archivo; repite el paper de descubrimiento. |
| M3 | `primera_medida` | TOI-210 b · radio | 2,234 ± 0,074 R⊕ | 2026-10-06 (0,62) | [2609.35747](https://arxiv.org/abs/2609.35747) | [ficha](http://localhost:3000/hallazgo/1f51ef98-3a73-4caa-83e3-29095beadbae) | no | Ausente del archivo; repite el paper de descubrimiento. |
| M4 | `primera_medida` | TOI-5699 b · radio | 0,996 ± 0,015 R♃ | 2026-10-07 (0,65) | [2609.30915](https://arxiv.org/abs/2609.30915) | [ficha](http://localhost:3000/hallazgo/80f1ee39-b99f-4dc2-9051-51e9af24adf6) | no | Ausente del archivo; repite el paper de descubrimiento. |
| M5 | `primera_medida` | RX J0534.0-0221 b · masa | 2,8 ± 0,5 M♃ | 2026-10-07 (0,60) | [2609.20748](https://arxiv.org/abs/2609.20748) | [ficha](http://localhost:3000/hallazgo/4de1dca5-72db-4aed-b06d-8b90f85b4ae2) | no | Ausente del archivo; repite el paper de descubrimiento. |
| M6 | `primera_medida` | TOI-5120 b · radio | 0,969 ± 0,017 R♃ | 2026-10-07 (0,65) | [2609.30915](https://arxiv.org/abs/2609.30915) | [ficha](http://localhost:3000/hallazgo/609b4f8e-22ba-49ad-983f-8357729fe661) | no | Ausente del archivo; repite el paper de descubrimiento. |
| M7 | `primera_medida` | TOI-5120 b · masa | 1,23 ± 0,05 M♃ | 2026-10-07 (0,65) | [2609.30915](https://arxiv.org/abs/2609.30915) | [ficha](http://localhost:3000/hallazgo/60b268a9-efb7-41bc-8fac-66448ecae2d1) | no | Ausente del archivo; repite el paper de descubrimiento. |
| M8 | `primera_medida` | TOI-5699 b · masa | 0,25 ± 0,03 M♃ | 2026-10-07 (0,65) | [2609.30915](https://arxiv.org/abs/2609.30915) | [ficha](http://localhost:3000/hallazgo/c8a732aa-b2ab-4ed7-8990-6190add1ad4f) | no | Ausente del archivo; repite el paper de descubrimiento. |
| M9 | `primera_medida` | HD 65216 c · masa | 3,32 +1,4/−1,16 M♃ | 2026-10-08 (0,58) | [2610.02783](https://arxiv.org/abs/2610.02783) | [ficha](http://localhost:3000/hallazgo/fff37edb-2a91-49cd-b366-8ada2626aeb5) | sí | Primera masa verdadera de un planeta catalogado: el archivo solo tenía m sin i (Wittenmyer 2019, 645 M⊕). Depende del modelo de inclinación. |
| M10 | `primera_medida` | HD 65216 b · masa | 2,23 +0,96/−0,77 M♃ | 2026-10-08 (0,62) | [2610.02783](https://arxiv.org/abs/2610.02783) | [ficha](http://localhost:3000/hallazgo/47acc87d-043d-4748-87ca-6829fc6283f2) | sí | Primera masa verdadera de HD 65216 b; antes, solo m sin i. Depende del modelo de inclinación. |
| M11 | `primera_medida` | OGLE-2006-BLG-284 b · masa | 165 +40/−33 M⊕ | 2026-10-09 (0,75) | [2610.07133](https://arxiv.org/abs/2610.07133) | [ficha](http://localhost:3000/hallazgo/6d9c491c-ea2a-4288-bb99-40b30a880c3e) | no | Ausente del archivo; repite el paper. |
| M12 | `primera_medida` | TOI-2211 c · masa | 8,53 ± 1,04 M⊕ (**m sin i** de un candidato, ver §2) | 2026-10-10 (0,70) | [2610.08922](https://arxiv.org/abs/2610.08922) | [ficha](http://localhost:3000/hallazgo/ab6940a9-5618-4192-ad1f-17db2c02d52f) | no (**error**) | m sin i de un candidato sin designación, publicada como «masa de TOI-2211 c»; el archivo no tiene ese planeta. |
| M13 | `primera_medida` | TOI-2211 b · masa | 2,69 ± 0,56 M⊕ | 2026-10-10 (0,70) | [2610.08922](https://arxiv.org/abs/2610.08922) | [ficha](http://localhost:3000/hallazgo/273bd9b0-48ea-4802-9f7e-c34bdbf19156) | sí | Primera masa de un planeta catalogado (en el archivo como TOI-2211.01, sin masa). |
| M14 | `primera_medida` | TIC 402710280 b · radio | 3,75 ± 0,22 R⊕ (**candidato**) | 2026-10-22 (0,60) | [2610.26056](https://arxiv.org/abs/2610.26056) | [ficha](http://localhost:3000/hallazgo/53276767-4ce1-4940-8a0a-002d1cfe1eb8) | no (**engañoso**) | Es un candidato, no un planeta; el archivo no lo tiene. |
| M15 | `confirmacion_independiente` | TOI-2158 b · masa | σ 0,56 | 2026-10-08 (0,45) | [2609.30915](https://arxiv.org/abs/2609.30915) | [ficha](http://localhost:3000/hallazgo/30365a4f-dd57-4f52-880b-99c35ca3d3a2) | no (**no independiente**) | Compara Knudstrup 2026 con Knudstrup 2022: mismo primer autor. La regla de T89 exige otro paper, no otro equipo. |
| M16 | `confirmacion_independiente` | HIP 67522 b · masa | σ 0,75 | 2026-10-08 (0,55) | [2609.35979](https://arxiv.org/abs/2609.35979) | [ficha](http://localhost:3000/hallazgo/0f104365-dc7f-485f-ab77-77e24c0be619) | no | Coincidencia con Chakraborty 2026 (13,8 ± 1,0 M⊕) a 0,7–1,4σ; correcta pero sin noticia. |
| M17 | `confirmacion_independiente` | TOI-2211 b · radio | σ 0,69 | 2026-10-10 (0,60) | [2610.08922](https://arxiv.org/abs/2610.08922) | [ficha](http://localhost:3000/hallazgo/320af417-2153-436b-ab57-c214aff5a569) | no | Coincide con Crossfield 2025; correcta pero sin noticia. |

### Opción u opciones elegidas para ADR 0039

Marca una o varias (se pueden combinar):

- [x] O1 · Seguir con las dos vías tal cual, en operación continua.
- [ ] O2 · Solo la vía del archivo y el resumen semanal: `[reader] measurement_categories = []` apaga `reader-v3`, `catalog_tension`, `primera_medida` y `confirmacion_independiente`.
- [ ] O3 · Solo la vía arXiv: `[solution_tensions] writer_enabled = false`.
- [x] O4 · Resumen semanal como producto principal.
- [ ] O5 · Hallazgos de medida como producto principal.
- [ ] O6 · Cambiar de enfoque: tarea nueva de definición de la fase siguiente.
- [ ] O7 · Pausar `run-night` hasta decidir.

Comentario del autor:

Decisión del autor (2026-11-03): O1 + O4 + T100 (corrección de los hallazgos de medida). ADR 0039.

Nota (2026-11-05): O6 se abordó después en T104 (ADR 0044, 2026-11-05).

## Consultas de referencia

Todas corren en una sesión de solo lectura: `docker exec -i nocturna-postgres psql -U nocturna -d nocturna -X -v ON_ERROR_STOP=1` con `SET default_transaction_read_only = on`.

- **R1 (registro por noche).** CTE sobre `runs` (desde 2026-09-30 00:00 Europe/Madrid hasta 2026-11-04) unida a:
  - `agent_calls` (Reader por `prompt_version`, redactor y Editor);
  - `readings` `reader-v3` con `jsonb_array_elements(measurements)`; utilizable = `origin = 'this_work'`, `limit = 'none'`, `err_plus` y `err_minus` no nulos;
  - `tension_evaluation` por `reading_id`;
  - `findings` por `run_id` y tipo.

  v3+ = `sum(tokens v3) − llamadas v3 × avg(tokens reader-v2)` por noche (media global si esa noche no hubo v2).
- **R2 (archivo).**

  ```sql
  SELECT (computed_at AT TIME ZONE 'Europe/Madrid')::date, count(*),
         count(*) FILTER (WHERE skip_reason IS NULL), count(item_id)
  FROM solution_tension GROUP BY 1 ORDER BY 1;
  ```
- **Población del muestreo.** `readings` con `prompt_version = 'reader-v3'` y `superseded_at IS NULL`, sin los 4 abstracts de T71.b, ordenadas por `md5('T78:' || external_id)`.
  - S1: `reading_id` presente en `tension_evaluation` con algún `findings.tension_evaluation_id`.
  - S2: el resto con `jsonb_array_length(measurements) > 0`.
  - S3: el resto con `items.exoplanet_match`.
- **Fichas.** `findings` con `type <> 'paper_explained'`, unidas a `tension_evaluation`, `solution_tension` y `archive_solution` (alta y referencia) por `new_solution_key` y `reference_solution_key`.
