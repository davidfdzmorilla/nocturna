# Fixture T79: filtro de ingesta de exoplanetas

`ep_items.json`: exportado el 2026-10-01 (solo lectura, SELECT) de la base real de Nocturna: los 25 ítems leídos con `reader-v3` (noches 30-sep y 1-oct) mas 2609.37597 (TOI-6981 b, status new), los 4 abstracts de `../t71b/abstracts.json` y 2 negativos sinteticos (`"synthetic": true`).

Campos: external_id, title, abstract, categories, label, expected_match.
`label` segun la clasificacion del informe del autor. `expected_match`: true = exoplanet_specific; false = solar_system y sinteticos; null = exoplanet_general (lo decide el autor en el checkpoint).
