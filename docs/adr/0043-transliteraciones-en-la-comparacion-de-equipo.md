# ADR 0043 · Transliteraciones en la comparación de equipo

Fecha: 2026-11-04 · Estado: aceptado · Tarea: T103 · Precisa: ADR 0040 (consecuencia «Independencia») · Relacionados: ADR 0023 §2

## Contexto

ADR 0040 §3 exige que el primer autor de la referencia no figure entre los autores del paper (`domain/team.py::shares_first_author`). La comparación solo normalizaba TeX y acentos. Así, «Müller» frente a «Mueller» daba equipo distinto: dirección arriesgada, porque podía generar una `confirmacion_independiente` que no lo es. ADR 0040 lo registró como limitación. T103 (deuda de T100) la cierra para la transliteración alemana, la escandinava, la serbocroata y `\dj`.

## Decisión

1. Tras `_normalize`, cada palabra del apellido y de los autores se pliega con `_fold`, en palabras de al menos 5 letras: `ue`→`u`, `oe`→`o`, `ae`→`a`, `aa`→`a`, `dj`→`d`. El pliegue se aplica igual a los dos lados (`_canonical`). La comparación sigue siendo por palabra completa.
2. El umbral de 5 letras evita plegar apellidos cortos. Xue/Xu, Yue/Yu, Yuen/Yun y Chae/Cha siguen distintos.
3. Normalización TeX y letras: `\dj` y `\DJ` pasan a `d` y `D`; `ẞ` pasa a `SS`. Müller = Mueller = Muller.
4. Las firmas públicas de `domain/team.py` no cambian.

**Propiedad.** El pliegue es una función de cada palabra, aplicada a ambos lados, y la comparación es por palabra completa. Por eso solo puede pasar un resultado de «equipo distinto» a «mismo equipo», nunca al revés. No crea `None` nuevos, porque ninguna palabra de 5 o más letras queda vacía. Todo falso positivo del pliegue suprime una confirmación; ninguno crea una.

**Falsos positivos aceptados.** Rafael/Rafał, Michael/Michał, Aerts/Arts. Palabras que colisionan tras el pliegue: Bauer y Baur, Samuel y Samul.

**No cubierto.**
- Palabras de menos de 5 letras: Baer/Bär y Loeb/Löb quedan como equipo distinto (dirección arriesgada).
- Otras transliteraciones: ć/ch, ž/zh, cirílico.
- Rotación del primer autor y coautores (el archivo no los da).
- Afiliaciones dentro de `<authors>`.
- Un refname que empieza por TeX (`{\AA}berg`, `\DJ{}…`) no se reconoce, porque `_REFERENCE_RE` exige letra inicial. El archivo da el refname en texto plano; el resultado es `None`, sin confirmación (dirección conservadora).

## Alternativas descartadas

- **D1, umbral.** 4 letras: cubre Baer/Loeb, pero junta Yuen/Yun y Chae/Cha. Sin umbral: junta también Xue/Xu.
- **D2, alcance.** Solo alemán y `\dj`→`d`, sin plegar «Dj» en texto plano.
- **D3, registro.** Solo `ARCHITECTURE.md` y `TECHNICAL_DEBT.md`, sin ADR. ADR 0040 dice que ue/oe/ae y `\dj` no se cubren; cambiarlo tiene alternativas reales y necesita registro propio.

## Consecuencias

- Solo `domain/team.py`, tests de dominio e I001 de `tests/db/test_cli_dry_run_digest_db.py`. Sin migración, sin cambio de API, de prompt ni de `config/`. Sin LLM ni tokens.
- Impacto medido en la base real el 2026-11-04 (solo lectura, script del autor): Q1, 0 confirmaciones con autores conocidos; Q2, 0 evaluaciones `evaluated` con autores en los últimos 30 días. Ningún resultado cambia.
- La regla de ADR 0040 §3 se mantiene. Cambia solo el alcance de la comparación. Lo no cubierto está en `docs/TECHNICAL_DEBT.md`.
