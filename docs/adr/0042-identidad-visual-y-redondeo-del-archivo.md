# ADR 0042 · Identidad visual y redondeo de las cifras del archivo

Fecha: 2026-11-04 · Estado: aceptado · Tarea: T102 · Relacionados: ADR 0024 (§5), 0028, 0041

## Contexto

Tras T101, la web no tenía identidad visual: los títulos se veían como enlaces por defecto, la portada repetía fecha y tipo en cada tarjeta, las cifras de `/archivo/[semana]` mostraban hasta ocho decimales y las tablas se desbordaban en móvil. El autor pidió el rediseño el 2026-11-04. El plan se aprobó con las decisiones D1–D8, resueltas el mismo día en `docs/OPEN_DECISIONS.md` (T102).

## Decisión

1. **Identidad oscura, solo modo oscuro (D7).** Los colores son tokens semánticos en `@theme` de Tailwind 4 (`web/src/app/globals.css`). El fichero anota el contraste WCAG 2.1 de cada par fondo/texto; todos los de texto son ≥ 4,5:1. Los bordes son decorativos.
2. **Tipografía del sistema (D1).** Sans de sistema para el cuerpo; serif de sistema para títulos y logotipo. No se descargan fuentes ni se añade dependencia.
3. **Logotipo y cabecera (D8).** Logotipo de texto «Nocturna» con un punto ámbar. La cabecera no marca la sección activa: el layout sigue siendo un Server Component.
4. **Feed agrupado por noche (D3, D4).** `lib/feedGroups.ts` agrupa elementos consecutivos del mismo día en `Europe/Madrid`, sin reordenar. El separador es «Publicados el 4 de noviembre de 2026», en un `<p>` con `<time>`, no en un encabezado. Las tarjetas van en rejilla de 1 columna en móvil y 2 desde `lg`, dentro de `max-w-5xl`.
5. **Tarjeta (D5).** Un solo enlace, el título, extendido a toda la tarjeta con `::after` (stretched link). La etiqueta de tipo va encima como eyebrow, también en `paper_explained`, según ADR 0024 §5. La fecha no se repite en cada tarjeta.
6. **Controles.** El filtro por tipo es una lista de píldoras y el selector de nivel es un control segmentado; ambos son enlaces reales (`?tipo=`, `?nivel=`), sin estado de cliente. La paginación deshabilitada se marca con `aria-disabled` y no lleva enlace.
7. **Tablas accesibles.** `components/TableScroll.tsx` envuelve las tablas de datos en una región con nombre (`role="region"`, `aria-labelledby` apuntando al `<caption>`), enfocable (`tabIndex=0`) y con desplazamiento horizontal propio. El anillo de foco es el global de `globals.css`.
8. **Cifras del archivo redondeadas (D2, D6).** Solo en `/archivo/[semana]`, con `formatArchiveValueRounded` (`lib/measurementFormat.ts`):
   - Con los dos errores > 0, el menor lleva dos cifras significativas y valor y errores comparten decimales. Si los errores redondeados coinciden, se muestra «±».
   - Sin errores, con error nulo o cero, o con cota: cuatro cifras significativas, sin ceros finales.
   - Nunca se redondea a la izquierda de la coma ni se usa separador de miles.
   - El valor exacto va en el `title` de la celda y en un `<details>` «Valores exactos del archivo» bajo cada tabla de cambios.
   - `formatArchiveValue` (espejo de `format_archive_value`) y `formatWithErrors` del detalle de hallazgos no cambian.

## Alternativas descartadas

- **D1, fuentes.** `next/font/google` (Inter y Source Serif 4; el build necesita red); `next/font/local` con woff2 OFL en el repo.
- **D2, redondeo.** Decimales fijos por unidad; separador de miles.
- **D3, rejilla.** Una columna siempre, con `max-w-3xl`.
- **D4, separador.** «Noche del 3 al 4 de noviembre».
- **D5, etiqueta de tipo.** Ocultarla en `paper_explained`; exigiría un ADR que modifique 0024.
- **D6, alcance del redondeo.** Aplicarlo también al detalle de hallazgos.
- **D7, modo claro.** Modo claro con `prefers-color-scheme`.
- **D8, navegación.** Marca de sección activa con `usePathname`, lo que obliga a un componente cliente en la cabecera.

## Consecuencias

- Solo web. Sin cambios en `backend/`, `config/`, `package.json` ni en la API; sin migración.
- El redondeo es decisión de presentación de la web y no tiene espejo en Python. Quien necesite el valor exacto lo encuentra en el `title` o en el `<details>`.
- La tipografía del sistema varía por plataforma: «Iowan Old Style» existe en macOS e iOS; en otros sistemas el título cae en Palatino, Georgia o el serif genérico.
- Sin modo claro: quien tenga el sistema en claro verá la web en oscuro.
- La cabecera no indica la sección activa.
- Quitar el anillo de foco global exige un sustituto `:focus-visible` (ya comentado en `globals.css`).
