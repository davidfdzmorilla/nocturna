---
name: ddd-conventions
description: Convenciones DDD del proyecto — qué va en domain, application e infrastructure, cómo se escriben entidades, repositorios y casos de uso, y qué se considera sobrearquitectura. Invocar al tocar backend/src/nocturna/domain o application.
---

# DDD en este proyecto: real pero mínimo

## Capas

```
domain/          entidades, value objects, errores, interfaces de repositorio, interfaz LLMProvider
application/     casos de uso (uno por fichero), BudgetGuard, orquestación de agentes, prompts
infrastructure/  SQLAlchemy, MCP servers, AgentSDKProvider, configuración, logging
api/             FastAPI (solo lectura), esquemas de respuesta
```

Dependencias solo hacia dentro: `api → application → domain`, `infrastructure → domain`. `domain/` no importa nada del proyecto fuera de sí mismo ni librerías de IO. El hook `guard-write.sh` lo comprueba para los imports más obvios; el `reviewer` lo comprueba para el resto.

## Entidades

- `@dataclass` (o Pydantic solo si aporta validación real). Identidad por `id: UUID`.
- Las transiciones de estado son métodos de la entidad con reglas dentro (`item.mark_read(reading)`, `finding.publish(confidence)`), no asignaciones sueltas desde fuera.
- Invariantes con excepciones de `domain/errors.py`: `InvalidTransition`, `InterestScoreOutOfRange`, `BudgetExceeded`, `OutsideExecutionWindow`.

## Repositorios

- Interfaz en `domain/repositories.py` como `Protocol`. Métodos con nombre de dominio (`items.next_unread(limit)`, `runs.current()`), no CRUD genérico.
- Implementación en `infrastructure/db/repositories.py`. Traduce entidad ↔ modelo ORM. El ORM no cruza hacia `application/`.

## Casos de uso

- Una clase por caso de uso en `application/use_cases/`, con `__call__` o `execute`. Reciben repositorios, `BudgetGuard` y `LLMProvider` por constructor. Sin acceso a configuración global: lo que necesitan se les pasa.
- Un caso de uso no llama a otro caso de uso salvo `RunNight`, que es el orquestador y sí compone `ReadItem`, `PopularizeReading`, `EditNight`.

## Unidad de trabajo

- Patrón de tres transacciones por llamada a agente: `(1) authorize + timeout_for_call` leve en la misma sesión; `(2) run_agent` fuera de toda transacción (retiene la conexión 0 segundos durante la espera); `(3) record_call + persistencia de Reading/Finding` en una tercera sesión. ADR 0006 § 2. Ninguna transacción retiene conexión durante `item_timeout_s = 180 s` esperando al modelo.

## Qué es sobrearquitectura aquí (y se rechaza)

- Eventos de dominio, buses, CQRS, agregados con raíces artificiales.
- Repositorios genéricos `BaseRepository[T]`.
- Interfaces con una sola implementación *y sin plan de segunda* (excepción explícita: `LLMProvider`, porque la segunda implementación está prevista por la política de suscripción).
- Carpetas `shared/`, `common/`, `utils/` con más de un fichero.
- Cualquier cosa "para la fase 2".
