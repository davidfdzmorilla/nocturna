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
"""

import asyncio
import logging
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKError, ResultError, ResultMessage, query

from nocturna.domain.errors import LLMError, LLMTimeout
from nocturna.domain.llm import AgentRequest, AgentResult

_logger = logging.getLogger(__name__)

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
    )


def _tokens_from_usage(usage: dict[str, Any] | None) -> tuple[int, int]:
    """Suma de tokens de un `usage` crudo (`dict[str, Any] | None`).

    `tokens_in` incluye caché (creación y lectura): `BudgetGuard` existe
    para no pasarse del presupuesto semanal, no para contabilidad fina, y
    la suscripción paga esos tokens igual que los de entrada normales.
    Los campos de caché son `int | None` en la API subyacente (ausentes si
    no se usó caché) — `or 0` los trata como cero sin propagar el `None` a
    la suma.
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
          `domain/errors.py`).
        - `ResultMessage` de éxito (`subtype == "success"`,
          `is_error=False`) cuyo `terminal_reason` no es `None` ni
          `"completed"` (p. ej. `"max_turns"`): el CLI lo cuenta como
          éxito, pero el texto de `result` puede estar incompleto, así que
          también se trata como fallo. `terminal_reason` ausente (CLI
          antiguo, o un resultado que no pasó por el bucle de turnos) no
          se penaliza.
        - `ResultMessage` de éxito sin `usage` o sin `result`: no hay forma
          fiable de contabilizar el gasto o de devolver un texto utilizable,
          así que se trata como fallo aunque el CLI lo marcara como éxito.
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
            tokens_in, tokens_out = _tokens_from_usage(
                last_result.usage if last_result is not None else None
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
            tokens_in, tokens_out = _tokens_from_usage(
                last_result.usage if last_result is not None else None
            )
            exc.tokens_in = tokens_in
            exc.tokens_out = tokens_out
            raise
        except ResultError as exc:
            in_flight_exception = True
            usage = (last_result.usage if last_result is not None else None) or (
                exc.data.get("usage") if exc.data else None
            )
            tokens_in, tokens_out = _tokens_from_usage(usage)
            status_suffix = (
                f" (api_error_status={exc.api_error_status})"
                if exc.api_error_status is not None
                else ""
            )
            raise LLMError(
                f"el agente '{request.role.value}' falló: {exc}{status_suffix}",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                api_error_status=exc.api_error_status,
            ) from exc
        except ClaudeSDKError as exc:
            in_flight_exception = True
            tokens_in, tokens_out = _tokens_from_usage(
                last_result.usage if last_result is not None else None
            )
            raise LLMError(
                f"el agente '{request.role.value}' falló: {exc}",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            ) from exc
        except Exception as exc:
            in_flight_exception = True
            tokens_in, tokens_out = _tokens_from_usage(
                last_result.usage if last_result is not None else None
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
            tokens_in, tokens_out = _tokens_from_usage(last_result.usage)
            status_suffix = (
                f", api_error_status={last_result.api_error_status}"
                if last_result.api_error_status is not None
                else ""
            )
            raise LLMError(
                f"el agente '{request.role.value}' terminó con "
                f"subtype='{last_result.subtype}', is_error={last_result.is_error}"
                f"{status_suffix}",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                api_error_status=last_result.api_error_status,
            )
        if last_result.terminal_reason not in (None, "completed"):
            tokens_in, tokens_out = _tokens_from_usage(last_result.usage)
            raise LLMError(
                f"el agente '{request.role.value}' terminó con subtype='success' "
                f"pero terminal_reason='{last_result.terminal_reason}': la salida "
                "puede estar incompleta",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        if last_result.usage is None:
            raise LLMError(
                f"el agente '{request.role.value}' terminó con éxito pero sin 'usage': "
                "no se puede contabilizar el gasto"
            )
        if last_result.result is None:
            tokens_in, tokens_out = _tokens_from_usage(last_result.usage)
            raise LLMError(
                f"el agente '{request.role.value}' terminó con éxito pero sin texto de salida",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )

        tokens_in, tokens_out = _tokens_from_usage(last_result.usage)
        return AgentResult(
            role=request.role,
            model=request.model,
            output_text=last_result.result,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
        )
