"""`AgentSDKProvider`: la única implementación activa de `LLMProvider` en fase 1.

Único módulo del proyecto que importa `query`/`ClaudeAgentOptions` de
`claude_agent_sdk` para ejecutar agentes (skill `agent-sdk-usage`; el
servidor MCP de arXiv, `infrastructure/mcp/arxiv_server.py`, es el otro
módulo que toca el SDK, pero solo para declarar herramientas, nunca para
llamar a `query()`). Construye las opciones de cada llamada, ejecuta
`query()` con un timeout duro, traduce el resultado a `AgentResult` o a una
excepción de dominio, y comprueba que no haya `ANTHROPIC_API_KEY` en el
entorno.

**Frontera deliberada.** Este módulo no importa `nocturna.application` ni
`nocturna.infrastructure.config`/`nocturna.infrastructure.db`. No lee
`config/pipeline.toml`, no conoce `BudgetGuard` y no construye ni persiste
`AgentCall`: todo eso llega ya resuelto en `AgentRequest` o es
responsabilidad de quien orquesta (T41-T44). Un test AST verifica esta
frontera.

**Inspección de la versión instalada (2026-09-16, `claude-agent-sdk`
0.2.153) frente a la documentación pública consultada la misma fecha:**

- `ResultMessage.usage` es `dict[str, Any] | None`, no un tipo estructurado
  `UsageData` como sugiere la documentación. Las claves (`input_tokens`,
  `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`) sí coinciden con lo documentado — confirmado
  leyendo `_internal/message_parser.py`, que copia el `usage` tal cual
  llega del JSON del CLI (el objeto `usage` crudo de la API de mensajes,
  en snake_case) — pero hay que leerlas con `.get()` sobre un `dict`, no
  como atributos.
- El SDK **sí** exporta señal de límite de tasa: `RateLimitEvent` y
  `RateLimitInfo` están en `__all__` de `claude_agent_sdk/__init__.py` y se
  definen en `types.py` con `status` (`allowed`/`allowed_warning`/
  `rejected`), `utilization`, `resets_at` y `rate_limit_type`. El CLI los
  emite como mensajes normales del mismo stream que recorre `run_agent`
  (junto a `AssistantMessage`, `ResultMessage`, etc.), no como excepción.
  Hoy el bucle de `run_agent` solo mira `isinstance(message, ResultMessage)`
  y descarta cualquier otro tipo, `RateLimitEvent` incluido. No se captura
  aquí a propósito: decidir la reacción (avisar, cortar, reintentar) es
  alcance de T41/T44, no de T40 (ver `LLMRateLimited` en `domain/errors.py`).
- `ResultMessage` no expone ningún id de modelo "efectivo" distinto del
  `model` que ya viaja en `AgentRequest`; no hay nada que decidir en el
  punto 9 del plan más allá de usar `request.model` tal cual.

**Hallazgo del humo manual (T40, 2026-09-17): `ResultMessage.usage`
subregistra el gasto real.** Volcado literal de una sola llamada al Reader
(`model="sonnet"`, una petición) contra `claude-agent-sdk` 0.2.153:

```
usage={'input_tokens': 523, 'cache_creation_input_tokens': 0,
       'cache_read_input_tokens': 0, 'output_tokens': 6, ...}
model_usage={
  'claude-haiku-4-5-20251001': {'inputTokens': 929, 'outputTokens': 17,
      'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0,
      'costUSD': 0.001014, ...},
  'claude-sonnet-5': {'inputTokens': 523, 'outputTokens': 6,
      'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0,
      'costUSD': 0.001106, ...},
}
total_cost_usd=0.0021200000000000004
```

`usage` solo reporta el modelo pedido (Sonnet: 523/6 tokens); el CLI gastó
además 946 tokens de Haiku por su cuenta (no pedidos por `AgentRequest`,
probablemente coste fijo de sesión), y ese gasto **sí se factura**: lo
confirma `0.001014 + 0.001106 = 0.00212 = total_cost_usd`. Contabilizar solo
`usage` registra el 36% de lo gastado en esta llamada — un subregistro de
2,8x. Esa cifra es de **una sola llamada de humo** con un prompt casi
vacío; no extrapola directamente a una noche completa, donde el peso
relativo de los 946 tokens fijos de Haiku frente al resto del gasto varía
con el tamaño de cada prompt real.

Para una noche completa, con `limits.max_calls_per_item = 2` (T30) y en el
peor caso (reintento en todos los ítems): `max_items_per_night = 40` ítems
× 2 llamadas + 2 llamadas del Editor = 82 llamadas. Asumiendo el mismo
coste fijo de 946 tokens invisibles por llamada del humo manual, son
82 × 946 = 77.572 tokens invisibles al `BudgetGuard`, que cortaría a
`nightly_tokens=300_000` registrados habiendo gastado 377.572 reales de
verdad — un déficit del 20,5%: exactamente el modo de fallo que
`budget.py` existe para impedir.

Verificado contra el paquete instalado, no de memoria: `model_usage` es
`dict[str, ModelUsage] | None` (`types.py`), donde `ModelUsage` es un
`TypedDict` — en runtime cada valor es un `dict` corriente, sin clase
propia, igual que ya pasaba con `usage`. `_internal/message_parser.py`
construye `ResultMessage.model_usage=data.get("modelUsage")` pasando el
payload crudo del CLI tal cual, sin traducir claves: por eso las claves de
cada entrada de `model_usage` son **camelCase** (`inputTokens`,
`outputTokens`, `cacheReadInputTokens`, `cacheCreationInputTokens`, más
`costUSD`/`contextWindow`/`maxOutputTokens`/`webSearchRequests` y, opcionales,
`canonicalModel`/`provider`), a diferencia del `usage` de nivel superior
(snake_case). `usage` y `model_usage` solapan (el Sonnet aparece en ambos
con los mismos 523/6): la corrección sube el gasto sumando **todas las
entradas de `model_usage`** cuando está disponible, nunca `usage` +
`model_usage` a la vez. `model_usage` puede venir `None` o `{}` (CLI
antiguo, o un camino que no lo informa): en ese caso se mantiene el
respaldo a `usage` con la misma política conservadora de siempre (los
campos de caché suman a `tokens_in`).
"""

import asyncio
import logging
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKError, ResultError, ResultMessage, query

from nocturna.domain.errors import LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentRequest, AgentResult

_logger = logging.getLogger(__name__)

#: Código HTTP que el CLI informa como `api_error_status` cuando la llamada
#: se rechaza por límite de tasa de la suscripción.
RATE_LIMIT_STATUS = 429


def _llm_error_class(status: int | None) -> type[LLMError]:
    """Elige la excepción a lanzar según `api_error_status`.

    `LLMRateLimited` (subclase de `LLMError`, `domain/errors.py`) señala un
    límite de tasa (`status == 429`); cualquier otro código, o su ausencia,
    usa `LLMError` sin más. Quien capture `LLMError` sigue atrapando ambas
    (`budget-guard-review` § 6: un límite de tasa termina la noche, no se
    reintenta; esa reacción es del consumidor, T44 — este proveedor solo da
    la señal tipada).
    """
    if status == RATE_LIMIT_STATUS:
        return LLMRateLimited
    return LLMError


#: Variables de entorno que este proveedor nunca tolera. Solo
#: `ANTHROPIC_API_KEY`: este proyecto corre contra la suscripción Claude Max
#: vía el CLI de Claude Code, nunca contra una API key (`CLAUDE.md`,
#: "Restricción que gobierna todo el diseño").
BLOCKED_ENV_VARS: tuple[str, ...] = ("ANTHROPIC_API_KEY",)


class ApiKeyInEnvironment(RuntimeError):
    """Hay una variable de `BLOCKED_ENV_VARS` en el entorno del proceso.

    No es `DomainError` (`domain/errors.py`): no es una regla de negocio
    incumplida, es un error de despliegue/entorno — la máquina está mal
    configurada para correr el pipeline, con independencia de cualquier
    `Run` o `Item`. Se comprueba en `AgentSDKProvider.__init__` y de nuevo
    al principio de cada `run_agent`, porque la variable puede aparecer a
    mitad de la noche (una shell interactiva del autor la exporta sin
    querer, por ejemplo) y ninguna llamada posterior debe colarse.
    """


def _check_no_blocked_env_vars() -> None:
    for var in BLOCKED_ENV_VARS:
        if var in os.environ:
            raise ApiKeyInEnvironment(
                f"la variable de entorno '{var}' está definida en este proceso. "
                "Nocturna corre contra la suscripción Claude Max vía el CLI de "
                "Claude Code, nunca contra una API key (CLAUDE.md). Quítala del "
                "entorno antes de arrancar el pipeline, por ejemplo con: "
                "env -u ANTHROPIC_API_KEY uv run nocturna run-night"
            )


def build_options(
    request: AgentRequest,
    *,
    mcp_servers: Mapping[str, Any],
    allowed_tools: Sequence[str],
) -> ClaudeAgentOptions:
    """Traduce un `AgentRequest` a `ClaudeAgentOptions`. Función pura.

    Testeable sin tocar el SDK real ni el entorno: no lee `os.environ`, no
    llama a `query()`, no construye nada asíncrono. La comprobación de
    `ANTHROPIC_API_KEY` vive aparte, en `_check_no_blocked_env_vars`,
    porque es un efecto de entorno y esta función no tiene ninguno.

    - `setting_sources=[]` **siempre explícito**, nunca omitido. El
      default del SDK (`None`) carga todas las fuentes de configuración del
      sistema de ficheros (user + project + local): `CLAUDE.md`,
      `.claude/settings.json`, los agentes de *desarrollo* de
      `.claude/agents/`. Eso es contexto de desarrollo, no del pipeline; si
      se omitiera este parámetro, cada `query()` de cada Item de cada
      noche facturaría ese contexto extra contra la suscripción sin que el
      autor lo pidiera. Omitir el parámetro (dejar que el SDK aplique su
      default) NO vale como equivalente a pasar `[]`.
    - `strict_mcp_config=True` siempre: solo se usan los servidores MCP que
      llegan por `mcp_servers`, nunca alguno que el CLI pudiera descubrir
      por su cuenta.
    - `permission_mode="default"` siempre. Nunca `"bypassPermissions"`: los
      agentes del pipeline no necesitan saltarse el sistema de permisos, y
      hacerlo sería la superficie más ancha posible para que un prompt mal
      formado (el abstract de un paper es texto no confiable) intente algo
      que no le corresponde.
    - `model` y `max_turns` salen de `request`, nunca hardcodeados ni
      releídos de `pipeline.toml` aquí: ese mapeo rol → modelo/turnos ya lo
      hizo quien construyó `AgentRequest` (T41-T43).
    - `system_prompt=request.system_prompt`, tal cual, sin transformar.
      Verificado contra el paquete instalado
      (`_internal/transport/subprocess_cli.py`,
      `SubprocessCLITransport._build_command`): con `request.system_prompt
      is None` (el default de `AgentRequest`) el argv resultante es **byte a
      byte idéntico** al de omitir el parámetro por completo —
      `ClaudeAgentOptions.system_prompt` ya tiene `None` como default en el
      SDK, así que pasarlo explícito no cambia nada; en ambos casos el CLI
      recibe `--system-prompt ""`, no `--system-prompt` ausente. Con un
      `str`, el CLI recibe `--system-prompt <str>`, que sustituye el system
      prompt entero (no lo añade a un preset): así separan
      `application/agents/` el prompt de rol del prompt de usuario (el
      abstract, texto no confiable) sin mezclarlos en un único bloque.
    - `tools=[]` **siempre explícito**, nunca omitido ni confundido con
      `allowed_tools=[]`. Verificado en
      `_internal/transport/subprocess_cli.py` del paquete instalado:
      `tools=None` (el default) no añade `--tools` al comando del CLI, así
      que el toolset por defecto del CLI (Bash, Read, Edit, ...) sigue
      disponible; solo `tools=[]` produce `--tools ""` y lo apaga de
      verdad. `allowed_tools` (con o sin contenido) solo controla qué
      herramientas quedan *preaprobadas* sin preguntar — no si existen.
    - `skills=[]` también explícito, pero con una corrección respecto a
      una verificación anterior de este mismo docstring: **con
      `setting_sources=[]` como se fija más abajo, `skills=[]` produce hoy
      un argv byte a byte idéntico a `skills=None`.** Comprobado leyendo
      `_apply_skills_defaults` en `subprocess_cli.py` del paquete
      instalado: con `skills=[]` el bucle que añade patrones
      `Skill(nombre)` a `allowed_tools` no itera nada (lista vacía) y el
      único efecto que le queda a `skills` no-`None` —forzar
      `setting_sources` a `["user", "project"]` cuando venía en `None`— no
      se dispara porque `setting_sources` ya no es `None` aquí, es `[]`
      explícito. Es decir: en esta configuración concreta, `skills=[]` no
      añade ninguna protección que `skills=None` no diera ya. Lo que sí es
      real y por eso se mantiene `skills=[]` explícito de todas formas
      —defensa en profundidad, no verificación de un efecto inexistente—
      es el **acoplamiento inverso**: si algún día se quita el
      `setting_sources=[]` de más abajo (dejándolo en el default `None`
      del SDK), `skills=[]` pasaría a **forzar**
      `setting_sources=["user", "project"]` en `_apply_skills_defaults`
      (la rama `if setting_sources is None: setting_sources = ["user",
      "project"]` sólo se salta cuando `skills is None`). Ese día,
      `skills=[]` cargaría `CLAUDE.md` y `.claude/settings.json` en cada
      llamada del pipeline sin que nadie lo note — justo lo que
      `setting_sources=[]` existe para evitar. Mantener `skills=[]` deja
      esa trampa neutralizada de antemano en vez de depender de que nadie
      toque la línea de `setting_sources` sin releer esta nota. En fase 1
      ningún rol necesita herramientas ni skills (el abstract viaja en el
      prompt), y el prompt lleva texto no confiable (el abstract de un
      paper), así que dejar el toolset por defecto disponible sería
      superficie de ataque gratuita y turnos/tokens que gastar sin
      necesidad.
    - `env`: se deja el default (`{}`), sin tocar. Verificado en
      `_internal/transport/subprocess_cli.py`: el proceso del CLI arranca
      con `{**inherited_env, **CLAUDE_CODE_ENTRYPOINT_default, **options.env}`,
      es decir `env` se **fusiona** sobre `os.environ` del proceso padre
      (menos `CLAUDECODE`), no lo sustituye; un `{}` no borra `PATH`/`HOME`
      ni el acceso del CLI a las credenciales de `~/.claude`. Esto no
      añade protección extra contra `ANTHROPIC_API_KEY`: la fusión hace
      que cualquier valor no vacío de `options.env` *gane* sobre el
      entorno heredado, así que si algún día `env` dejara de estar vacío,
      la variable tendría que evitarse ahí explícitamente — la
      comprobación de `_check_no_blocked_env_vars` solo inspecciona
      `os.environ` del proceso, no este campo. Hoy no es un problema
      porque `env` se deja vacío.
    - `max_budget_usd` no se usa: el presupuesto del proyecto es en tokens
      y vive en `BudgetGuard` (`application/budget.py`); un segundo tope en
      dólares sería una segunda verdad sobre el mismo gasto.
    - `cwd` se deja el default (el del proceso que llama, normalmente la
      raíz del repo): no se fija uno neutro a propósito. Con `tools=[]` no
      hay toolset base (Bash/Read/Edit/...) que pueda leer o escribir nada
      en ese directorio, `strict_mcp_config=True` impide que el CLI
      descubra un `.mcp.json` del cwd por su cuenta, y `setting_sources=[]`
      ya excluye la configuración de proyecto/local que el CLI cargaría
      desde ahí. Fijar un `cwd` distinto exigiría además un directorio que
      exista en disco (`subprocess_cli.py` lo comprueba y falla si no),
      complejidad sin ganancia de superficie una vez cerrados los otros
      tres frentes. Si algún rol futuro recupera herramientas, esta
      decisión habría que revisarla.
    """
    return ClaudeAgentOptions(
        model=request.model,
        max_turns=request.max_turns,
        mcp_servers=dict(mcp_servers),
        allowed_tools=list(allowed_tools),
        tools=[],
        skills=[],
        strict_mcp_config=True,
        permission_mode="default",
        setting_sources=[],
        system_prompt=request.system_prompt,
    )


def _tokens_from_usage(usage: dict[str, Any] | None) -> tuple[int, int]:
    """Suma de tokens de un `usage` crudo (`dict[str, Any] | None`).

    `tokens_in` incluye caché (creación y lectura): `BudgetGuard` existe
    para no pasarse del presupuesto semanal, no para contabilidad fina, y
    la suscripción paga esos tokens igual que los de entrada normales.
    Los campos de caché son `int | None` en la API subyacente (ausentes si
    no se usó caché) — `or 0` los trata como cero sin propagar el `None` a
    la suma.

    Solo el gasto del modelo pedido en `AgentRequest` (`usage` es el
    objeto `usage` de la API de mensajes para esa única llamada). No
    incluye el gasto de otros modelos que el CLI use por su cuenta dentro
    de la misma sesión (p. ej. Haiku) — para eso está
    `_tokens_from_model_usage`, que esta función respalda cuando
    `model_usage` no está disponible (ver docstring de módulo, hallazgo del
    humo manual de T40).
    """
    if usage is None:
        return 0, 0
    tokens_in = (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
    )
    tokens_out = int(usage.get("output_tokens") or 0)
    return tokens_in, tokens_out


def _tokens_from_model_usage(
    model_usage: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[int, int] | None:
    """Suma de tokens sobre **todos** los modelos de `model_usage`, o `None`.

    `model_usage` (`ResultMessage.model_usage`, `dict[str, ModelUsage] |
    None` en `types.py` de `claude-agent-sdk` 0.2.153) es el desglose de
    gasto por modelo de toda la sesión del CLI, no solo del modelo pedido
    en `AgentRequest`: el humo manual de T40 mostró una llamada al Reader en
    Sonnet cuyo `usage` reportaba 523/6 tokens, mientras `model_usage`
    llevaba además 929/17 tokens de Haiku que el CLI gastó por su cuenta
    (facturados: la suma de `costUSD` de ambas entradas coincide con
    `total_cost_usd`). Sumar solo `usage` subregistra ese gasto frente a
    `BudgetGuard`.

    `ModelUsage` es un `TypedDict` — en tiempo de ejecución cada valor de
    `model_usage` es un `dict` corriente, sin clase propia — y sus claves
    son **camelCase** (`inputTokens`, `outputTokens`,
    `cacheReadInputTokens`, `cacheCreationInputTokens`), a diferencia de
    `usage` (snake_case): `_internal/message_parser.py` pasa
    `data.get("modelUsage")` tal cual desde el JSON del CLI, sin traducir
    claves. Se soporta explícitamente esta forma (`dict`/`Mapping` con
    claves camelCase), verificada contra el paquete instalado, no la
    dataclass estructurada que sugeriría el nombre `ModelUsage`.

    Devuelve `None` (no `(0, 0)`) cuando `model_usage` es `None` o `{}`, para
    que `_extract_tokens` sepa que no hay desglose por modelo y debe
    respaldarse en `usage` en vez de comparar contra él. Bajo el `max()`
    componente a componente de `_extract_tokens`, un `(0, 0)` real sería
    equivalente en resultado (los tokens no son negativos, así que `usage`
    ganaría el `max()` igual): el centinela `None` no cambia el número final,
    solo evita que `_extract_tokens` tenga que decidir "¿esto es un desglose
    real de cero, o la ausencia de desglose?" — más claro como tipo de
    retorno explícito que como un caso especial de `(0, 0)`.

    Misma política conservadora que `_tokens_from_usage`: los campos de
    caché de cada entrada suman a `tokens_in`, y se leen con `or 0` porque
    pueden venir ausentes o `None`.
    """
    if not model_usage:
        return None
    tokens_in = 0
    tokens_out = 0
    for entry in model_usage.values():
        tokens_in += (
            int(entry.get("inputTokens") or 0)
            + int(entry.get("cacheCreationInputTokens") or 0)
            + int(entry.get("cacheReadInputTokens") or 0)
        )
        tokens_out += int(entry.get("outputTokens") or 0)
    return tokens_in, tokens_out


def _extract_tokens(
    *,
    usage: dict[str, Any] | None,
    model_usage: Mapping[str, Mapping[str, Any]] | None,
) -> tuple[int, int]:
    """Punto único de extracción de tokens: máximo componente a componente
    entre `model_usage` y `usage`, con `usage` de respaldo si `model_usage`
    no está disponible.

    `model_usage` (todos los modelos que gastó el CLI en la llamada) y
    `usage` (solo el modelo pedido) solapan — el modelo de `AgentRequest`
    aparece en ambos con las mismas cifras — así que nunca se suman
    (sumar ambos doblaría ese solape; lo cubre
    `test_usage_y_model_usage_solapados_no_se_suman_dos_veces`). Cuando
    ambos están disponibles se toma el `max()` de `tokens_in` y de
    `tokens_out` por separado, no una preferencia estricta por
    `model_usage`: en el volcado real `model_usage` es superconjunto de
    `usage` (mismas cifras del modelo pedido más lo que el CLI gastó por su
    cuenta), así que en el caso normal el máximo coincide con `model_usage`
    sin más; pero si algún día llegara un `model_usage` parcial cuyo total
    quedara por debajo del de `usage`, una preferencia estricta por
    `model_usage` *reduciría* el registro frente a usar solo `usage` — justo
    lo contrario de la política del proyecto ante ambigüedad
    (sobreestimar, nunca infraestimar, `CLAUDE.md`). El `max()` no puede
    doblar la contabilidad (es máximo, no suma) y cuesta lo mismo que la
    preferencia estricta.

    Se usa en todo punto de `run_agent` donde hoy se leen tokens de un
    `ResultMessage` o del payload de un `ResultError`, para que ningún
    camino (éxito, timeout, cancelación, `ResultError`, `ClaudeSDKError`,
    excepción no traducida, o los chequeos tras el bucle) se quede con el
    subregistro descrito en el docstring de módulo.
    """
    tokens_in_usage, tokens_out_usage = _tokens_from_usage(usage)
    from_model_usage = _tokens_from_model_usage(model_usage)
    if from_model_usage is None:
        return tokens_in_usage, tokens_out_usage
    tokens_in_model, tokens_out_model = from_model_usage
    return max(tokens_in_usage, tokens_in_model), max(tokens_out_usage, tokens_out_model)


class AgentSDKProvider:
    """`LLMProvider` que ejecuta agentes con `claude-agent-sdk` sobre el CLI local.

    `mcp_servers` y `allowed_tools` se inyectan por constructor, vacíos por
    defecto: en fase 1 ningún rol recibe servidor MCP (el abstract viaja en
    el prompt, no como llamada a herramienta), pero la interfaz no lo
    excluye para cuando eso cambie.
    """

    def __init__(
        self,
        *,
        mcp_servers: Mapping[str, Any] | None = None,
        allowed_tools: Sequence[str] | None = None,
    ) -> None:
        _check_no_blocked_env_vars()
        self._mcp_servers: dict[str, Any] = dict(mcp_servers) if mcp_servers else {}
        self._allowed_tools: list[str] = list(allowed_tools) if allowed_tools else []

    async def run_agent(self, request: AgentRequest) -> AgentResult:
        """Ejecuta `request` en una `query()` nueva y devuelve su `AgentResult`.

        Una conversación nueva por llamada (`query()` de un solo disparo,
        sin `ClaudeSDKClient`): ningún agente del pipeline acumula contexto
        entre ítems (`CLAUDE.md`, "Agentes (fase 1)").

        **`output_text` sale de `ResultMessage.result`, no de recorrer los
        `AssistantMessage` del stream.** El CLI ya agrega ahí el texto
        final de la conversación; reconstruirlo a mano recorriendo bloques
        de `AssistantMessage` (filtrando `TextBlock` de `ToolUseBlock`/
        `ThinkingBlock` y concatenando a través de posibles varios turnos)
        sería repetir ese trabajo con más superficie de fallo, y en fase 1
        `build_options` fija `tools=[]` (ver ese docstring), así que no hay
        turnos intermedios que perder. Si un rol futuro necesitara
        herramientas con varios turnos, esta elección habría que revisarla.

        **Fallos, todos como `LLMError`/`LLMTimeout` con los tokens ya
        gastados cuando se conocen** (nunca un `AgentResult` con tokens a
        cero "por si acaso"), propagados en todo camino de error donde haya
        un `usage` disponible (del `ResultMessage` visto en el stream, o del
        payload de `ResultError`):

        - Ningún `ResultMessage` recibido.
        - `ResultMessage` con `subtype != "success"`, o con
          `is_error=True` aunque `subtype == "success"` — el CLI usa esa
          combinación para reportar errores de la API (429/500/529) que
          interrumpieron el turno sin que el propio agente fallara; el
          mensaje de la excepción incluye `api_error_status` cuando el CLI
          lo informa, y también viaja como atributo
          `LLMError.api_error_status` (no solo en el texto: estas
          excepciones "se capturan por su nombre, no por texto",
          `domain/errors.py`). Cuando `api_error_status == 429` la excepción
          lanzada es `LLMRateLimited` (subclase de `LLMError`,
          `_llm_error_class`), no `LLMError` a secas: sigue siendo
          atrapable por `except LLMError`, pero quien orquesta (T44) puede
          distinguir un límite de tasa de cualquier otro fallo de API y
          cortar la noche sin reintentar (`budget-guard-review` § 6), en vez
          de tratarlo como el reintento habitual de JSON inválido. Este
          mismo mapeo se aplica también en la rama `except ResultError` más
          abajo, con `exc.api_error_status`.
        - `ResultMessage` de éxito (`subtype == "success"`,
          `is_error=False`) cuyo `terminal_reason` no es `None` ni
          `"completed"` (p. ej. `"max_turns"`): el CLI lo cuenta como
          éxito, pero el texto de `result` puede estar incompleto, así que
          también se trata como fallo. `terminal_reason` ausente (CLI
          antiguo, o un resultado que no pasó por el bucle de turnos) no
          se penaliza.
        - `ResultMessage` de éxito sin `usage` **ni** `model_usage` (los dos
          ausentes o vacíos a la vez: `None` o `{}` en cualquier
          combinación), o sin `result`: no hay forma fiable de contabilizar
          el gasto o de devolver un texto utilizable, así que se trata como
          fallo aunque el CLI lo marcara como éxito — sin esta rama, un
          `usage={}` con `model_usage` ausente devolvería en silencio un
          `AgentResult` de éxito con 0 tokens para una llamada que sí
          ocurrió. El SDK no acopla ambos campos (`_internal/message_parser.py`
          los construye con dos `.get()` independientes sobre el mismo frame): un frame
          con `model_usage` poblado y `usage=None` es utilizable y no cae
          por esta rama.
        - `asyncio.timeout` agotado por el deadline propio: `TimeoutError`
          con `cm.expired() is True` → `LLMTimeout`. `cm.expired()`
          (`asyncio.Timeout.expired()`) es API pública documentada desde
          Python 3.11, no un mecanismo interno: distingue el `TimeoutError`
          del propio deadline de `request.timeout_s` de un `TimeoutError`
          ajeno (p. ej. de un socket subyacente) que la misma clase también
          puede levantar dentro del bloque desde que ambos comparten tipo
          en 3.11. Un `TimeoutError` ajeno (`cm.expired() is False`) no es
          "el agente no respondió a tiempo": se traduce como cualquier otra
          excepción no reconocida, `LLMError` encadenada con `from exc`, no
          `LLMTimeout`.
        - Cualquier excepción de `claude_agent_sdk` (`ClaudeSDKError` y sus
          subclases, incluida `ResultError`, que el SDK lanza tras emitir
          el `ResultMessage` de error de un `query()` de un solo disparo)
          → `LLMError` encadenada con `from exc`.
        - Cualquier otra excepción que no sea `ClaudeSDKError` ni
          `asyncio.CancelledError`: el paquete instalado (`claude-agent-sdk`
          0.2.153) lanza `Exception` pelados en varios puntos del recorrido
          del stream (`_internal/query.py`, p. ej. `receive_messages` al
          traducir un mensaje `{"type": "error"}` del CLI) que no heredan
          de `ClaudeSDKError`; sin esta rama, escaparían sin traducir a
          `LLMError` y, si ya había llegado un `ResultMessage` con `usage`
          antes de la excepción, esos tokens se perderían sin registrar
          ningún `AgentCall` — con el patrón `except LLMError:` que T41
          usará para el reintento de JSON inválido, el intento tampoco
          contaría para el cortacircuitos `CALL_LIMIT_REACHED`. Se traduce
          igual que `ClaudeSDKError`, con los tokens del último `usage`
          conocido.

        **`asyncio.CancelledError` nunca se traduce a `LLMTimeout` ni a
        ningún otro error de dominio: se relanza tal cual.** Distingue "me
        cancelaron desde fuera" (el corte de `hard_stop`, T44 marca el Run
        `killed`) de "el modelo tardó más de `request.timeout_s`" (este
        método marca el ítem `timeout` y la noche sigue). Como
        `CancelledError` hereda de `BaseException` desde Python 3.8, ningún
        *otro* `except` de este método (todos sobre subclases de
        `Exception`, incluida la captura genérica del final de la cadena) lo
        captura por accidente. Hay un `except asyncio.CancelledError`
        explícito más abajo, pero no contradice lo anterior: existe para
        adjuntarle los tokens ya gastados antes de relanzarla, no para
        traducirla a un error de dominio ni para retenerla; el `raise` sin
        argumentos de esa rama es justo lo que mantiene la excepción
        "tal cual". Si ya había
        llegado un `ResultMessage` con `usage` antes de la cancelación, ese
        gasto no puede viajar en la firma de `run_agent` (no se traduce a
        una excepción propia) ni en un atributo del proveedor (rompería con
        llamadas concurrentes sobre la misma instancia): se adjunta como
        `tokens_in`/`tokens_out` en la propia instancia de
        `CancelledError` que se relanza, mismo nombre que en
        `LLMError`, para que T44 pueda leerlo sin que la excepción deje de
        ser exactamente la misma que levantó la cancelación.

        El `finally` que cierra el generador (`agen.aclose()`) respeta la
        misma regla: si `aclose()` levanta `CancelledError`, se relanza
        siempre, incluso por encima de una `LLMTimeout`/`LLMError`/
        `CancelledError` que estuviera en vuelo — el corte de `hard_stop`
        tiene que propagarse intacto por este camino también (T44). Si
        `aclose()` levanta cualquier otra excepción mientras ya había una
        excepción en vuelo (`LLMTimeout`, `LLMError` o el propio
        `CancelledError` que este método deja pasar), esa excepción de
        cierre se descarta (se registra con `logging`, nunca `print`) para
        no sustituir a la excepción original y perder los tokens que
        transporta — el mismo fallo de forma que T30 ya corrigió en
        `BudgetGuard.record_call`. Si no había ninguna excepción en vuelo
        (la iteración terminó con éxito), un fallo de `aclose()` sí se
        propaga: en ese caso es la única señal de que algo fue mal.

        **Detección de "excepción en vuelo" con una bandera local, no con
        `sys.exc_info()`.** `sys.exc_info()` refleja el estado de
        excepción del *hilo*, no del frame de este método: si quien llama a
        `run_agent` está a su vez dentro de un `except` (p. ej. el
        reintento de JSON inválido que exige `CLAUDE.md`, casi siempre
        escrito dentro del `except` del intento anterior), `sys.exc_info()`
        no es `None` aunque este método no haya lanzado nada — un fallo de
        `aclose()` en el camino de éxito se descartaría en silencio en vez
        de propagarse, justo el caso que el párrafo anterior dice que debe
        propagarse. Por eso `in_flight_exception` es una variable local
        puesta explícitamente a `True` en cada rama `except` de este
        método (incluida la de `CancelledError`, para que un fallo de
        `aclose()` durante una cancelación no sustituya al `CancelledError`
        por error) y nunca se lee `sys.exc_info()`.
        """
        _check_no_blocked_env_vars()

        options = build_options(
            request, mcp_servers=self._mcp_servers, allowed_tools=self._allowed_tools
        )

        started_at = time.monotonic()
        # `last_result` se sobreescribe con cada `ResultMessage` visto en el
        # stream y solo se usa el último. Correcto hoy porque en fase 1
        # `query()` emite exactamente un `ResultMessage` por llamada — cierto
        # para esta configuración (`tools=[]`, sin `mcp_servers`, sin hooks,
        # prompt `str`), no en general: `_internal/query.py` (líneas
        # ~837-856 en `claude-agent-sdk` 0.2.153) documenta que "a result
        # frame ends one turn, not necessarily the run: background tasks
        # keep running past it". Si T41 pasa `mcp_servers`/`allowed_tools`
        # no vacíos a este proveedor, `query()` puede emitir más de un
        # `ResultMessage` en la misma llamada y quedarse solo con el usage
        # del último infracontaría los tokens de los anteriores. Revisar
        # este punto (acumular en vez de sobreescribir) antes de activar
        # herramientas o servidores MCP para algún rol.
        last_result: ResultMessage | None = None
        in_flight_exception = False
        agen = query(prompt=request.prompt, options=options)
        try:
            async with asyncio.timeout(request.timeout_s) as cm:
                async for message in agen:
                    if isinstance(message, ResultMessage):
                        last_result = message
        except TimeoutError as exc:
            in_flight_exception = True
            tokens_in, tokens_out = _extract_tokens(
                usage=last_result.usage if last_result is not None else None,
                model_usage=last_result.model_usage if last_result is not None else None,
            )
            if not cm.expired():
                # `TimeoutError` ajeno al deadline propio (p. ej. de un
                # socket subyacente): desde Python 3.11 comparte clase con
                # el que levanta `asyncio.timeout`, pero `cm.expired()`
                # (API pública desde 3.11) los distingue. No es "el agente
                # no respondió en Ns", así que no se traduce a
                # `LLMTimeout`: se trata como cualquier otra excepción no
                # reconocida.
                raise LLMError(
                    f"el agente '{request.role.value}' falló con un TimeoutError "
                    f"ajeno al deadline de {request.timeout_s}s: {exc}",
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                ) from exc
            raise LLMTimeout(
                f"el agente '{request.role.value}' no respondió en {request.timeout_s}s",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            ) from None
        except asyncio.CancelledError as exc:
            in_flight_exception = True
            tokens_in, tokens_out = _extract_tokens(
                usage=last_result.usage if last_result is not None else None,
                model_usage=last_result.model_usage if last_result is not None else None,
            )
            exc.tokens_in = tokens_in
            exc.tokens_out = tokens_out
            raise
        except ResultError as exc:
            in_flight_exception = True
            # `usage` y `model_usage` se eligen aquí con dos `or`
            # independientes, así que en teoría podrían emparejar el
            # `usage` de un frame con el `modelUsage` de otro. Hoy no es
            # defecto: `exc.data` es el mismo frame crudo que el bucle ya
            # parseó como `last_result` (verificado en
            # `_internal/query.py`: `ResultError` se construye a partir del
            # último frame de resultado visto, no de uno posterior), así
            # que cuando ambos lados de cada `or` están poblados, coinciden.
            # Revisar esta suposición si T41 habilita MCP/herramientas y
            # `query()` pasa a emitir más de un `ResultMessage` por llamada
            # (ver el comentario sobre `last_result` más arriba, ~línea
            # 506): ese cambio podría separar el frame de `last_result` del
            # que trae `exc.data`.
            usage = (last_result.usage if last_result is not None else None) or (
                exc.data.get("usage") if exc.data else None
            )
            model_usage = (last_result.model_usage if last_result is not None else None) or (
                exc.data.get("modelUsage") if exc.data else None
            )
            tokens_in, tokens_out = _extract_tokens(usage=usage, model_usage=model_usage)
            status_suffix = (
                f" (api_error_status={exc.api_error_status})"
                if exc.api_error_status is not None
                else ""
            )
            raise _llm_error_class(exc.api_error_status)(
                f"el agente '{request.role.value}' falló: {exc}{status_suffix}",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                api_error_status=exc.api_error_status,
            ) from exc
        except ClaudeSDKError as exc:
            in_flight_exception = True
            tokens_in, tokens_out = _extract_tokens(
                usage=last_result.usage if last_result is not None else None,
                model_usage=last_result.model_usage if last_result is not None else None,
            )
            raise LLMError(
                f"el agente '{request.role.value}' falló: {exc}",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            ) from exc
        except Exception as exc:
            in_flight_exception = True
            tokens_in, tokens_out = _extract_tokens(
                usage=last_result.usage if last_result is not None else None,
                model_usage=last_result.model_usage if last_result is not None else None,
            )
            raise LLMError(
                f"el agente '{request.role.value}' falló con una excepción del SDK "
                f"no traducida ({type(exc).__name__}): {exc}",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            ) from exc
        finally:
            try:
                await agen.aclose()
            except asyncio.CancelledError:
                raise
            except Exception as close_exc:
                if in_flight_exception:
                    _logger.warning(
                        "agen.aclose() falló para el agente '%s' mientras se "
                        "propagaba otro error; se descarta para no enmascarar "
                        "la excepción original y los tokens que transporta",
                        request.role.value,
                        exc_info=close_exc,
                    )
                else:
                    raise

        duration_ms = int((time.monotonic() - started_at) * 1000)

        if last_result is None:
            raise LLMError(f"el agente '{request.role.value}' terminó sin emitir ResultMessage")
        if last_result.subtype != "success" or last_result.is_error:
            tokens_in, tokens_out = _extract_tokens(
                usage=last_result.usage, model_usage=last_result.model_usage
            )
            status_suffix = (
                f", api_error_status={last_result.api_error_status}"
                if last_result.api_error_status is not None
                else ""
            )
            raise _llm_error_class(last_result.api_error_status)(
                f"el agente '{request.role.value}' terminó con "
                f"subtype='{last_result.subtype}', is_error={last_result.is_error}"
                f"{status_suffix}",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                api_error_status=last_result.api_error_status,
            )
        if last_result.terminal_reason not in (None, "completed"):
            tokens_in, tokens_out = _extract_tokens(
                usage=last_result.usage, model_usage=last_result.model_usage
            )
            raise LLMError(
                f"el agente '{request.role.value}' terminó con subtype='success' "
                f"pero terminal_reason='{last_result.terminal_reason}': la salida "
                "puede estar incompleta",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        tokens_in, tokens_out = _extract_tokens(
            usage=last_result.usage, model_usage=last_result.model_usage
        )
        if not last_result.usage and not last_result.model_usage:
            # Ambas fuentes ausentes o vacías a la vez: no hay ningún dato
            # con el que contabilizar el gasto (a diferencia de `usage`
            # ausente/vacío con `model_usage` poblado, que sí es utilizable
            # vía `_extract_tokens` — ver el hallazgo del bloqueante de T40:
            # el SDK no acopla los dos campos). `not usage` cubre tanto
            # `usage is None` como `usage == {}`: un `ResultMessage` de
            # éxito con `usage={}` y `model_usage` ausente no lleva ningún
            # tokens_in/tokens_out real que contabilizar, así que cae aquí
            # igual que `usage is None`, en vez de devolver en silencio un
            # `AgentResult` de éxito con 0 tokens para una llamada que sí
            # ocurrió. `tokens_in`/`tokens_out` son 0 aquí por construcción
            # (ambas fuentes vacías), se pasan igual que en las otras ramas
            # para mantener la forma uniforme.
            raise LLMError(
                f"el agente '{request.role.value}' terminó con éxito pero sin 'usage' "
                "ni 'model_usage': no se puede contabilizar el gasto",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        if last_result.result is None:
            raise LLMError(
                f"el agente '{request.role.value}' terminó con éxito pero sin texto de salida",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

        return AgentResult(
            role=request.role,
            model=request.model,
            output_text=last_result.result,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
        )
