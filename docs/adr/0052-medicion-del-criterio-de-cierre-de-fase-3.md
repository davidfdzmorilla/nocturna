# ADR 0052 · Medición del criterio de cierre de la fase 3

Fecha: 2026-11-10 · Estado: aceptado · Tarea: T110.a · Relacionados: ADR 0021, 0044, 0045, 0048, 0051

## Contexto

ADR 0044 fija el criterio de cierre de la fase 3 en cinco puntos, (a) a (e), y la ventana de medición de 14 días (D7). Deja la medición a T110, en solo lectura. Sin reglas fijas antes de medir, cada punto admite más de una lectura, y la lectura se elegiría a la vista del resultado.

El precedente es ADR 0021 D10: los umbrales se fijan antes de medir y no se reinterpretan después.

Este ADR precisa cuatro puntos de ADR 0044 sin cambiar sus umbrales:

- (a) dice «13 o más de 14 comprobaciones diarias consecutivas». Aquí son 13 de los 14 días de la ventana, y un día sin comprobación cuenta como fallo (D3).
- (b) dice «el VPS refleja el último `Run` cerrado antes de las 09:00». Aquí la comprobación debe ser anterior a las 09:00 del día N (D4).
- (e) dice «14 noches consecutivas con informe matinal entregado». Aquí se cuenta desde `morning-deliveries.log` (D5).
- (c) y (d) se miden a mano. Su procedimiento se fija aquí.

ADR 0044 D6 fija el vigía exterior: la fecha «datos actualizados» del pie de la web, que muestra `/data-status` (ADR 0051 D5).

## Decisión

Decisión del autor del 2026-11-10 sobre el plan de T110: D1–D13 con la recomendación. Los umbrales y procedimientos de abajo son los del plan.

### Criterio (a): URL pública con TLS

- **D2, comprobación.** Una comprobación es un `GET https://<dominio>/` sin seguir redirecciones, con verificación TLS estándar (almacén de confianza del sistema, sin `-k` ni equivalentes) y un tope de 30 s, en un solo intento. Una comprobación es OK si responde 200, el certificado es válido y la respuesta no lleva `noindex`, ni en la cabecera `X-Robots-Tag` ni en `<meta name="robots">`. Cualquier `error` distinto de `-` hace fallar la comprobación, aunque la respuesta sea 200: también `ProbeTimeout` (el tope total de 30 s) y `ProbeBodyTooLarge` (un cuerpo de más de 2 MB, que no permite comprobar el `noindex`).
- **D3, cómo cuenta un día.** Cuenta la primera comprobación automática con fecha local d. Las manuales no cuentan. Un día sin comprobación automática falla.
- **Umbral:** 13 o más días OK de los 14 de la ventana.

### Criterio (b): el VPS refleja el último Run

- **D4, cumplimiento.** El día N cumple si hay una comprobación con fecha local N y `checked_at` anterior a las 09:00 de N que trae en el pie un `dateTime` mayor o igual al `finished_at` del Run de referencia.
- **Run de referencia.** Es el último `Run` de noche (`is_night_run`) cerrado antes de la comprobación. Puede ser anterior a la ventana: se lee de la base sin límite inferior. Se filtra por `started_at` (el candidato empieza antes de la comprobación y ha cerrado `finished_at` antes de ella). Como las noches no se solapan, ordenar por inicio o por fin da el mismo `Run`.
- **Sin Run de referencia.** Si no existe ningún `Run` de noche previo en toda la base, el día falla: no hay nada que reflejar.
- **Noche sin `Run`.** Una noche sin `Run` cumple si el VPS refleja el Run de referencia de la regla anterior.
- **Comprobaciones del día anterior.** Solo cuentan las comprobaciones con fecha local N y `checked_at` anterior a las 09:00 de N. Una del día anterior no cuenta para N.
- **Manuales.** Las comprobaciones manuales sí cuentan en (b), a diferencia de (a): basta una cualquiera anterior a las 09:00 de N que cumpla.
- Sin comprobación anterior a las 09:00 de N, el día falla.
- **Umbral:** 12 o más días cumplidos de 14.

### Criterio (e): informe matinal por noche

- **D5, cumplimiento.** Se cuenta desde `morning-deliveries.log` (ADR 0045). Cada noche N necesita una línea con `notified=ok` para N.
- Si el estado de la noche es distinto de `completed` (o `sin-run`), hace falta además una línea con `generated_at` anterior a las 09:00 de N, `notified=ok` y causas distintas de `-` y de `unclassified`.
- Un Mac apagado hasta pasadas las 09:00 cuenta como fallo, sin excepciones. Así lo fijó T105 como límite físico que se anota para T110.
- **Umbral:** 14 de 14 noches.

### Criterio (c): credenciales e imágenes (manual)

Se mide al inicio y al final de la ventana:

- tests de credenciales e imágenes de T107 (`test_deploy_no_claude_credentials.py` y `-m docker` con `NOCTURNA_CHECK_IMAGES=1`);
- inspección del VPS según `docs/DESPLIEGUE.md` §8;
- grep del volcado según `docs/DESPLIEGUE.md`, Sincronización §7.

- **D6, registro.** Solo se copian recuentos y booleanos. Nunca se copia `docker compose config` completo, porque incluye valores de configuración que no deben salir del VPS.
- **Umbral:** cero credenciales de Claude en las imágenes, en el compose y en el VPS, y salida vacía del grep de Sincronización §7 (`grep` sale con 1).

### Criterio (d): accesibilidad en producción (manual)

- **D7, medición.** Lighthouse de accesibilidad ≥ 90 en escritorio y en móvil, en `/`, en un `/hallazgo/<id>`, en `/archivo` y en `/archivo/<semana>`. Se mide una vez en los tres últimos días de la ventana. Se repite si se redespliega la web.

### Ventana de medición

- **D8, inicio.** Empieza la primera noche N cuyas 00:00 caen después de completar los pasos 1–14 de la checklist de T110.b. La fecha N se anota antes de empezar.
- Los días anteriores a N, sin VPS, no cuentan. No hay medición retroactiva.
- La ventana son los 14 días N a N+13. Se mide tras las 09:00 de N+13.

### Fallo

- **D9, parada.** La ventana se para en cuanto ya no hay remedio: con el primer día fallido en (e), con el segundo en (a), con el tercero en (b).
- Se abre la tarea de corrección que exige ADR 0044 D8.
- Tras la corrección, se abre una ventana nueva completa. La ventana anterior no se reanuda.

### Otras decisiones

- **D10, URL.** `NOCTURNA_PUBLIC_URL` va en las `EnvironmentVariables` de los plist del Mac, no en el repositorio ni en `config/pipeline.toml`.
- **D11, dónde se lanza.** `phase3-probe` se lanza desde los envoltorios de `run-night` (tras la sincronización) y del informe matinal.
- **D12, vigía.** No hay vigía de terceros. El vigía exterior es la fecha del pie (ADR 0044 D6).
- **D13, licencia.** La licencia (OD T00) no bloquea la puesta pública si T107 D9 se resuelve con una clave de despliegue de solo lectura y el repositorio sigue privado.

### Instrumento

- `nocturna phase3-probe [--trigger run-night|scheduled|manual]`. Códigos de salida: 0 registrado, 78 no configurado, 1 error de log. No abre la base de datos. Escribe una línea por comprobación con fecha local, instante, `trigger`, resultado, código HTTP, validez TLS, presencia de `noindex` y `dateTime` del pie.
- Semántica de `tls` y `error` en la línea de registro. `tls=ok` solo si la respuesta llegó por https, es decir, si la verificación pasó. Sin respuesta, `tls=fail` y `error=` nombra la causa: la subclase de `ssl.SSLError` de la cadena de causas si TLS falló (p. ej. `SSLCertVerificationError`), `ProbeTimeout`, `ProbeBodyTooLarge` o la clase de httpx (`ConnectError`, `ReadTimeout`...). `error=-` es condición necesaria para contar OK en (a).
- Tope total de 30 s en Python para la ejecución de la probe, con lectura en streaming y corte de tamaño de la respuesta. Los envoltorios añaden un vigilante de 90 s, para que una probe colgada nunca impida el informe matinal ni, por tanto, (e).
- `nocturna phase3-check --from YYYY-MM-DD [--days 14]`. Códigos de salida: 0 cumple (a), (b) y (e); 3 no cumple; 2 incompleta; 1 error. Solo ejecuta `SELECT` sobre la base y lee ficheros. No mide (c) ni (d).
- Con `--days` distinto de 14, `phase3-check` avisa en la salida. Si la ventana está incompleta pero un criterio ya no puede cumplir, imprime «ya no puede cumplir»; el código de salida sigue siendo 2. Es lo que apoya la parada temprana de D9.
- Registro `phase3-probes.log` en `NOCTURNA_LOG_DIR`, permisos 0600. No contiene la URL, el DSN ni rutas.
- Plantilla `docs/CIERRE_FASE_3.md`, que se rellena al cierre con los recuentos de (a), (b), (e) y los registros de (c) y (d).

## Alternativas descartadas

- **Cualquier 200 para (a) (D2).** Un 200 de otra página o de una respuesta sin TLS válido no prueba que la web esté publicada.
- **Job de launchd propio para la probe (D11).** Un agente más que vigilar. Los envoltorios que ya existen cubren las noches y el informe matinal.
- **Vigía de terceros (D12).** Expone la URL y el dominio a un servicio externo. El pie ya da la fecha sin ese coste.
- **URL en `config/pipeline.toml` (D10).** Llevaría la dirección de producción al repositorio.
- **Medición retroactiva (D8).** Mezclaría días sin el VPS con días medidos y no probaría la ventana.

## Consecuencias

- Los umbrales quedan fijados antes de medir. Un cambio posterior exige un ADR nuevo y no se aplica a la ventana en curso.
- Ningún criterio se cumple por este ADR. Su aceptación fija reglas. El instrumento lo construye T110.a y el cumplimiento se mide en T110.b.
- Un fallo de la probe en el Mac no se distingue de un VPS caído. Ambos son una comprobación fallida y se ven igual en el registro.
- Sin vigía de terceros (D12), una caída del VPS solo se detecta si el Mac hace la comprobación o si el autor mira el pie. Un Mac apagado se cuenta como fallo (D5).
- El trabajo de la ventana depende del Mac: sesión, red y ventana de ejecución. Un día con Mac apagado falla (a) y (b) de ese día, y (e) de esa noche.
- Nada de este ADR implica gasto de tokens, cambio de `budget.py` ni llamadas a Claude. Solo lee la base y ficheros, y hace peticiones HTTP a la web pública.
