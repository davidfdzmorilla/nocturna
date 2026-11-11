# Cierre de fase 3 · plantilla (T110.a)

Plantilla del informe de cierre de la fase 3. Criterio (a) a (e) en [ADR 0044](adr/0044-fase-3.md); umbrales, días y procedimientos en [ADR 0052](adr/0052-medicion-del-criterio-de-cierre-de-fase-3.md). Se rellena durante la ventana de 14 días (T110.b) y al cierre. Todavía no contiene datos.

- Solo se copian recuentos y booleanos. No se copian la URL, el DSN, rutas ni la salida completa de `docker compose config`.
- Fechas en `Europe/Madrid`, formato ISO.
- Las comprobaciones manuales no cuentan para (a) (ADR 0052 D3). Sí cuentan para (b).

## §1 · Ventana

| Campo | Valor |
|---|---|
| Fecha N de inicio | (vacía; se anota antes de empezar, ADR 0052 D8) |
| Pasos 1–14 de T110.b completados el | (vacío) |
| Inspección (c) inicial | (vacía) |
| Lighthouse (d) | (vacía; en los tres últimos días, N+11 a N+13) |
| Medición automática | tras las 09:00 de N+13 |
| Estado | (en curso / parada en N+k por (x) / cerrada) |

## §2 · Registro de 14 días

Una fila por día. Las columnas a, b y e salen de `uv run nocturna phase3-check --from N`: `ok` o `NO`. El motivo copia el de la salida.

| Fecha | a | b | e | Motivo |
|---|---|---|---|---|
| N (día 0) | | | | |
| N+1 (día 1) | | | | |
| N+2 (día 2) | | | | |
| N+3 (día 3) | | | | |
| N+4 (día 4) | | | | |
| N+5 (día 5) | | | | |
| N+6 (día 6) | | | | |
| N+7 (día 7) | | | | |
| N+8 (día 8) | | | | |
| N+9 (día 9) | | | | |
| N+10 (día 10) | | | | |
| N+11 (día 11) | | | | |
| N+12 (día 12) | | | | |
| N+13 (día 13) | | | | |

Totales: (a) __/14, umbral 13 · (b) __/14, umbral 12 · (e) __/14, umbral 14.

## §3 · Criterios

### (a) URL pública con TLS

- **Umbral**: 13 o más días OK de los 14 (ADR 0052 D2 y D3).
- **Qué cuenta**: la primera comprobación automática del día local. Un día sin comprobación automática falla.
- **Procedimiento**: tras las 09:00 de N+13, `cd backend && uv run nocturna phase3-check --from N`. Se copia la columna a y el recuento.
- **Registro**: `(a) __/14`, motivos de los días fallidos.

### (b) El VPS refleja el último `Run`

- **Umbral**: 12 o más días cumplidos de 14 (ADR 0052 D4).
- **Qué cuenta**: una comprobación con fecha local N y hora anterior a las 09:00 de N, cuyo pie es igual o posterior al `finished_at` del último `Run` de noche cerrado antes de la comprobación.
- **Procedimiento**: el mismo comando que (a). Se copia la columna b y el recuento.
- **Registro**: `(b) __/14`, motivos de los días fallidos.

### (c) Credenciales e imágenes (manual)

- **Umbral**: cero credenciales de Claude en las imágenes, en el compose y en el VPS; salida vacía del grep de Sincronización §7 (`grep` sale con código 1).
- **Procedimiento**: al inicio (tras el paso 11 de T110.b) y al final (tras las 09:00 de N+13):
  1. `cd backend && uv run pytest tests/test_deploy_no_claude_credentials.py`. Se copian pasados y fallidos.
  2. `cd backend && NOCTURNA_CHECK_IMAGES=1 uv run pytest -m docker tests/deploy/test_images.py`. Se copian pasados y fallidos.
  3. Inspección del VPS según `docs/DESPLIEGUE.md` §8. Se copia un booleano por punto.
  4. Grep del volcado según `docs/DESPLIEGUE.md`, Sincronización §7. Se copia «sin coincidencias: sí/no».

| Medición | Fecha | Pasados / total | Resultado |
|---|---|---|---|
| Tests de credenciales (inicio) | | | |
| Tests de imágenes (inicio) | | | |
| Inspección del VPS §8 (inicio) | | | |
| Grep de Sincronización §7 (inicio) | | | |
| Tests de credenciales (final) | | | |
| Tests de imágenes (final) | | | |
| Inspección del VPS §8 (final) | | | |
| Grep de Sincronización §7 (final) | | | |

### (d) Accesibilidad en producción (manual)

- **Umbral**: Lighthouse de accesibilidad ≥ 90 en escritorio y en móvil, en cada una de las cuatro URLs (ADR 0052 D7).
- **Procedimiento**: una vez en los tres últimos días de la ventana (N+11 a N+13). Se usa un `<id>` de hallazgo publicado y no retirado, y una `<semana>` de `/archivo`. Se repite si se redespliega la web.

| URL | Escritorio | Móvil | Fecha |
|---|---|---|---|
| `/` | | | |
| `/hallazgo/<id>` | | | |
| `/archivo` | | | |
| `/archivo/<semana>` | | | |

### (e) Informe matinal por noche

- **Umbral**: 14 de 14 noches (ADR 0052 D5).
- **Qué cuenta**: una línea con `notified=ok` por noche en `morning-deliveries.log`. Un Mac apagado hasta pasadas las 09:00 cuenta como fallo.
- **Procedimiento**: el mismo comando que (a). Se copia la columna e y el recuento.
- **Registro**: `(e) __/14`, motivos de las noches fallidas.

## §4 · Incidencias

| Fecha | Criterio | Descripción | Causa | Acción (tarea) |
|---|---|---|---|---|
| | | | | |

## §5 · Parada (D9)

La ventana se para en cuanto ya no hay remedio (ADR 0052 D9):

- Con el primer día fallido en (e).
- Con el segundo día fallido en (a).
- Con el tercer día fallido en (b).

`phase3-check` imprime «ya no puede cumplir» con los días ya cerrados. La tabla de §2 manda: la parada se anota aquí, con la fecha y el criterio.

Si la ventana se para: se abre la tarea de corrección que exige ADR 0044 D8. Tras la corrección, se abre una ventana nueva completa. La ventana anterior no se reanuda.

## §6 · Opciones de cierre

Para decisión del autor. Marcar una.

- [ ] O1 · Cumple: (a) ≥ 13, (b) ≥ 12, (e) 14, (c) y (d) dentro de umbral. Se escribe el ADR de cierre de fase 3.
- [ ] O2 · Parada (D9): se abre la tarea de corrección y una ventana nueva completa (ADR 0052 D9).
- [ ] O3 · (c) o (d) fuera de umbral con (a), (b) y (e) cumplidos: se corrige la causa y se repite la medición de ese criterio. El cierre espera a la repetición.

## Referencia

- Comando: `cd backend && uv run nocturna phase3-check --from N` (`--days 14` por defecto; con otro valor avisa). Códigos: 0 cumple, 3 no cumple, 2 incompleta, 1 error. Solo lee la base y los registros.
- Registros, en `NOCTURNA_LOG_DIR` (por defecto `~/nocturna-logs`): `phase3-probes.log` (0600, sin URL) y `morning-deliveries.log`.
- Procedimiento del Mac: `docs/CALIBRACION.md`, Paso 9.
- Procedimiento del VPS: `docs/DESPLIEGUE.md`, §8 y Sincronización §7.
