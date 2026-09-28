# ADR 0013 · La atribución de medidas a planetas pasa al Reader (vía (c))

Fecha: 2026-09-28 · Estado: aceptado, condicionado a la prueba manual de T71.b · **Completa ADR 0012 §11** (activa la vía (c) que allí quedaba sujeta a decisión expresa del autor) · **Deja abierto** el criterio de cierre de fase 2 (ADR 0012 §9)

## Contexto

T71 ejecutó el experimento de viabilidad el 2026-09-28 sobre los 74 ítems de astro-ph.EP con `Reading` (5 noches), con 42 peticiones al NASA Exoplanet Archive y cero tokens:

- Emparejamiento de nombres: 20 como planeta, 4 como anfitriona de un solo planeta, 12 como anfitriona multiplanetaria (ambiguo), 146 sin emparejar.
- Valor del paper disponible: vía (a), parser determinista, 1 caso; vía (b), solución propia ya ingerida, 0 casos.
- Casos con σ ≥ 2: 1 (σ = 4,88). **Falso positivo** verificado a mano: la masa (2,8 M_J) pertenece a RX J0534.0-0221 b, planeta nuevo que no está en el archivo, y se atribuyó a TWA 7 b, citado en el abstract solo como comparación.

La revisión manual de los abstracts con cifras encontró dos casos que la regla determinista perdió por citar más de un planeta:

- TOI-2109 b (2609.26894): el paper repite los valores del archivo (Wong et al. 2021). σ = 0, sin tensión.
- V1298 Tau b y e (2609.30038): masas del orden de 0,4–0,7 M_J frente a la solución por defecto del archivo (Livingston et al. 2026: 0,041 ± 0,017 y 0,048 ± 0,013 M_J), unas 3σ; compatibles con Suárez Mascareño et al. 2022. Cálculo manual y orientativo.

El cuello de botella no es el catálogo ni la lectura de números, sino **atribuir cada número al planeta al que pertenece**: los abstracts citan planetas de comparación o varios planetas a la vez. Una regla determinista falla en los dos sentidos (un falso positivo, dos casos reales perdidos).

## Decisión

1. **Se adopta la vía (c)**: el Reader extrae, además de lo que ya extrae, medidas estructuradas por planeta (planeta, parámetro, valor, errores, unidad). Python sigue haciendo el contraste y el cálculo de σ (ADR 0012 §3): Claude atribuye, no calcula.
2. **Antes de construir nada**, una prueba manual (T71.b, marcador `-m manual`, `NOCTURNA_ALLOW_REAL_CLAUDE=1`) sobre los abstracts de T71 con cifras, con gasto acotado de antemano, comprueba que la atribución es correcta. Si falla, se vuelve a esta decisión.
3. **El criterio de cierre de fase 2 (ADR 0012 §9: al menos 10 candidatos en dos semanas) se revisa**: el ritmo observado es de una tensión real en cinco noches. El nuevo criterio queda como decisión abierta del autor.

## Consecuencias

- Cambia el esquema de salida y el prompt del Reader (versión nueva de prompt) y su gasto de salida por ítem de astro-ph.EP: toca un agente, así que la tarea que lo implemente lleva doble revisión con `budget-guard-review`.
- El parser determinista de T71 queda como herramienta del experimento, no como base de T73.
- T72–T78 se revisan tras T71.b: T73 toma el valor del paper de la salida del Reader en lugar de un parser.

## Alternativas descartadas

- **Afinar la regla determinista de atribución**: tres iteraciones de revisión sobre el parser mostraron que cada heurística nueva abre otro caso; la atribución necesita comprensión del texto.
- **Replantear la definición de descubrimiento o parar la fase 2**: el experimento muestra que la señal existe (V1298 Tau) y que el fallo está localizado en un paso concreto.
