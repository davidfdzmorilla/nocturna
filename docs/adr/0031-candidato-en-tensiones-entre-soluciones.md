# ADR 0031 · "Candidato" en las tensiones entre soluciones

Fecha: 2026-10-14 · Estado: aceptado · Tarea: T85.w · Relacionados: ADR 0012 §6, ADR 0024 §5, ADR 0029, ADR 0030

## Contexto

ADR 0012 §6 pide que la web marque como "candidato" toda discrepancia calculada que ninguna persona ha revisado. ADR 0024 §5 concretó esa regla cuando solo existía `catalog_tension`: la etiqueta "Candidato" aparece solo en ese tipo. T85.e añadió `solution_tension`, la diferencia entre dos soluciones del NASA Exoplanet Archive. También la calcula el pipeline y nadie la revisa, así que la regla de ADR 0024 §5 se queda corta.

## Decisión

1. **Dónde va la etiqueta.** "Candidato" aparece en `catalog_tension` y en `solution_tension` (`isCandidate` en `web/src/lib/findingTypes.ts`). Esto amplía ADR 0024 §5, que no se edita.
2. **Leyenda propia por tipo** (`candidateLegend(type)`):
   - `catalog_tension` conserva la suya.
   - `solution_tension` lleva "Candidato: discrepancia entre soluciones del NASA Exoplanet Archive calculada automáticamente; ninguna persona la ha verificado."
3. **Presentación del tipo** (decisiones de T85.w, D1–D13, OD de T85.w):
   - **Etiqueta y filtro.** La etiqueta es "Tensión entre soluciones", con el slug `tension-soluciones`. En el filtro va justo después de "Tensión con el catálogo".
   - **Tabla principal.** Muestra la solución nueva y la de referencia con sus valores y errores en las unidades de la API (M⊕, R⊕, d), sin convertir. La solución por defecto del archivo lleva su marca, y la referencia, una frase fija con su rol.
   - **Otros datos del bloque.**
     - La σ con su umbral y una nota fija sobre lo que mide.
     - Las etiquetas `model_dependent` ("Depende del modelo") y `controversial` ("Planeta controvertido"), cada una con su frase.
     - La fecha de entrada de la alta en el archivo.
     - Todas las demás soluciones con su σ, en el orden de la API.
     - La ficha del planeta.
   - **Nota de origen.** El detalle añade una nota que dice que el hallazgo no procede de un artículo de arXiv.
4. **Sin reglas de negocio en la web.** No calcula `extreme_sigma`, la razón entre valores ni conversiones de unidades, y las etiquetas que no conoce no se pintan (`knownLabels`). La nota fija sobre la σ cubre los casos extremos sin copiar el umbral de `pipeline.toml`.

## Consecuencias

- Los dos tipos de tensión calculada se presentan igual de prudentes.
- `writer_enabled` se puede activar tras el merge de T85.w: la web ya sabe pintar el tipo.
- Los títulos de las tablas de datos de los cuatro tipos de medida empiezan ahora con mayúscula (`parameterTitle`).

## Alternativas descartadas

- **Sin "Candidato" en `solution_tension`**: contradice ADR 0012 §6.
- **Mostrar `extreme_sigma` en la web**: obliga a copiar el umbral de la configuración o a ampliar la API y su contrato.
