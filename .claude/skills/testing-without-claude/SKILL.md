---
name: testing-without-claude
description: Cómo testear código que depende de LLMProvider sin llamar a Claude — FakeLLMProvider, escenarios de fallo, marcador manual, fixtures de arXiv. Invocar al escribir tests en backend/tests.
---

# Tests sin tocar la suscripción

## FakeLLMProvider

`backend/tests/fakes/llm.py`. Implementa `LLMProvider` y se configura por agente:

```python
fake = FakeLLMProvider()
fake.respond("reader", json=valid_reading_dict, tokens_in=1200, tokens_out=300)
fake.respond("reader", raw="```json\n{not valid", tokens_in=1200, tokens_out=50)   # JSON inválido
fake.fail("popularizer", error=LLMRateLimited())                                   # límite alcanzado
fake.respond("editor", json={"publish": [...]}, tokens_in=8000, tokens_out=600)
```

- Guarda cada llamada recibida (`fake.calls`) para afirmar sobre el orden, el modelo pedido y el número de llamadas (por ejemplo: el Editor se llamó exactamente una vez).
- Los tokens declarados se usan para probar `BudgetGuard`: con un presupuesto de 2 000 y respuestas de 1 200+300, la segunda llamada debe rechazarse.

## Reloj y ventana

- `FakeClock(now=datetime(...))` inyectado en `BudgetGuard`. Tests: dentro de ventana, a las 04:44, a las 04:46.

## Base de datos

- Fixture `db_session` con base de datos de test (`nocturna_test`) en el mismo PostgreSQL de compose; `alembic upgrade head` una vez por sesión de tests; rollback por test.
- Tests de dominio no usan esta fixture.

## Marcador manual

- `pyproject.toml`: `[tool.pytest.ini_options] markers = ["manual: llama a Claude de verdad; solo a mano"]` y `addopts = "-m 'not manual'"`.
- Único test permitido con `@pytest.mark.manual`: `tests/manual/test_sdk_smoke.py`, que hace una llamada mínima, comprueba que se registran tokens en `AgentCall` y que `ANTHROPIC_API_KEY` no está en el entorno. El autor lo lanza con `uv run pytest -m manual`.

## arXiv

- Fixtures en `tests/fixtures/arxiv/`: una respuesta Atom con 3 entradas, una vacía, una con un `id` ya existente (dedup). El cliente HTTP se sustituye por `respx` o un transporte falso de `httpx`; nunca red real en tests.

## Qué es un test suficiente para "Hecho cuando"

- Cada viñeta de "Hecho cuando" en `PLAN_TAREAS.md` tiene al menos un test con nombre reconocible. Si una viñeta no es testeable (por ejemplo "Lighthouse ≥ 90"), se documenta cómo se verificó a mano en el cierre de la tarea.
