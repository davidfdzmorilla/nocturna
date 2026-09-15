# ADR 0001 · El análisis corre contra la suscripción Max vía Agent SDK

Fecha: 2026-09-15 · Estado: aceptado

## Contexto

El autor tiene una suscripción Claude Max personal con capacidad no usada de noche. Quiere que el pipeline la aproveche en lugar de pagar API por tokens. La política de Anthropic al respecto ha cambiado varias veces en 2026: prohibición de OAuth de suscripción en terceros (febrero), restricción de harnesses externos (abril), anuncio de crédito separado para Agent SDK (mayo) y pausa de ese cambio el mismo día de su entrada en vigor (15 de junio). A fecha de este ADR, el Agent SDK y `claude -p` siguen consumiendo los límites de la suscripción, y Anthropic ha dicho que avisará antes de cualquier cambio.

## Decisión

- El proveedor LLM de fase 1 es `AgentSDKProvider` sobre `claude-agent-sdk`, autenticado con el CLI de Claude Code logueado en la cuenta Max.
- Sin `ANTHROPIC_API_KEY` en el proyecto. Los hooks lo bloquean.
- El pipeline corre en la ventana 00:00–04:45 (una sesión de cinco horas que se cierra antes de que el autor empiece a trabajar) con presupuesto fijo del 30% del límite semanal, calibrado empíricamente.
- La web es de solo lectura y nunca llama a Claude, para no enrutar tráfico de terceros por la suscripción.
- `LLMProvider` es una interfaz desde el primer día para poder cambiar a `ApiKeyProvider` por configuración si la política cambia.

## Alternativas descartadas

- **API key desde el principio**: coste por token no justificado mientras la suscripción tenga capacidad ociosa; se mantiene como plan B implementable en una tarea.
- **Llamar a `/v1/messages` con el token OAuth de la suscripción**: prohibido por Anthropic; además solo el tráfico del SDK/CLI genuino se factura contra el plan.
- **Usar la cuenta Team del trabajo**: es de la empresa, los límites son por usuario y no se comparten, y sería un problema de términos y de relación laboral.

## Consecuencias

- El control de gasto es la pieza más crítica del sistema y se revisa dos veces.
- La política externa puede cambiar con aviso; el coste de adaptación está acotado a un proveedor nuevo y un valor de configuración.
- El pipeline compite con el uso interactivo del autor en el límite semanal; el 30% es un punto de partida, no un compromiso.
