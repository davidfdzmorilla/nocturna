# ADR 0041 · Resumen semanal del archivo en la portada

Fecha: 2026-11-04 · Estado: aceptado · Tarea: T101 · Origen: ADR 0039, O4 · Relacionados: ADR 0017, 0028, 0038

## Contexto

ADR 0039 (O4, 2026-11-03) fijó el resumen semanal del NASA Exoplanet Archive como producto principal y estable de la web. Hasta T101, `/` mostraba solo el feed de hallazgos publicados; el resumen estaba en `/archivo` y `/archivo/[semana]`.

## Decisión

1. **Orden de la portada.** `/` muestra primero el resumen semanal de la semana más reciente con cambios de `/archive/weeks`, esté cerrada o no. La API no marca semanas cerradas, y no se añade ese campo.
2. **Contenido compacto.** Si hay texto del redactor (`writer_text`), título y resumen con la etiqueta «Resumen redactado por IA» y una leyenda propia de portada, que remite a la semana completa para los datos. Después, los recuentos de la semana y enlaces a `/archivo/<semana>` y a `/archivo`. Sin tablas.
3. **Nota bajo el resumen.** Con texto del redactor: «Los recuentos salen del NASA Exoplanet Archive con plantillas fijas, sin texto generado por IA.» Sin texto: la nota de `digestNote` sin texto (plantillas fijas, sin texto de IA). Constantes propias, no derivadas de la nota de `/archivo/[semana]`.
4. **Estados.**
   - Si la lista de semanas falla: aviso de error en el bloque; el feed sigue.
   - Sin semanas con cambios: aviso con enlace a `/archivo`.
   - Sin texto del redactor, o con fallo del detalle de la semana: la semana se muestra con recuentos y enlaces, sin bloque de IA.
   - No se busca una semana anterior con texto.
5. **Sin endpoint nuevo.** La portada usa `GET /archive/weeks` y, para la semana destacada, `GET /archive/weeks/{week}`.
6. **Feed debajo.** El feed sigue en `/`, en la sección `#hallazgos`, con las mismas URLs (`/?page=`, `/?tipo=`). El resumen aparece también en las páginas siguientes del feed.
7. **Cabecera, meta-descripción y pie.** Cabecera: «Nocturna · Resumen del archivo · Hallazgos» (enlace `/#hallazgos`). Meta-descripción y pie: «Nocturna. Resumen semanal del NASA Exoplanet Archive y hallazgos generados automáticamente a partir de arXiv astro-ph.»
8. **Banner de IA.** Se mantiene en el layout, como en el resto de rutas.

## Consecuencias

- Cada visita a `/` hace dos llamadas más a la API que antes: la lista de semanas (`ListDigestWeeks`, una consulta por semana, ADR 0028; ver `TECHNICAL_DEBT.md`) y el detalle de la semana destacada. La página sigue en `force-dynamic`.
- De viernes a lunes, la semana destacada puede no tener texto del redactor, que llega la noche del lunes (ADR 0038). La portada muestra entonces recuentos y enlaces.
- Sin cambios en `backend/`, `config/` ni dependencias. Sin migración.
- Cambia lo primero que ve el lector de `/`: el resumen del archivo, antes que los hallazgos.

## Alternativas descartadas

- **Endpoint nuevo para la portada.** Duplicaría la lógica de `/archive/weeks` sin necesidad hoy.
- **«Última semana cerrada» como criterio.** La API no marca semanas cerradas; añadir un campo `closed` quedó descartado.
- **Buscar una semana anterior con texto** cuando la destacada no lo tiene. Mostraría un texto de otra semana distinto de los recuentos que lo acompañan.
- **Mover el feed a otra ruta.** Rompería las URLs actuales de `/` (`?page=`, `?tipo=`).
- **Tablas del archivo en la portada.** Repiten `/archivo/<semana>`.
