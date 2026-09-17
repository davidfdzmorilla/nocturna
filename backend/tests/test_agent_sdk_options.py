"""`build_options` es pura: traduce un `AgentRequest` a `ClaudeAgentOptions`
sin tocar el SDK real ni el entorno (`agent_sdk_provider.build_options`,
docstring). Estos tests no parchean `query` ni necesitan la guarda
`_no_claude`: no ejecutan ninguna conversación.

El punto central de la tarea es `setting_sources == []`: el default del SDK
(`None`) carga `CLAUDE.md`, `.claude/settings.json` y los agentes de
desarrollo de `.claude/agents/`, contexto que no debe facturarse contra la
suscripción en cada llamada del pipeline nocturno.
"""

from nocturna.domain.llm import AgentRequest, AgentRole
from nocturna.infrastructure.llm.agent_sdk_provider import build_options


def _request(**overrides: object) -> AgentRequest:
    defaults: dict[str, object] = dict(
        role=AgentRole.READER,
        model="claude-sonnet-4-5",
        prompt="lee este abstract",
        max_turns=3,
        timeout_s=60,
    )
    defaults.update(overrides)
    return AgentRequest(**defaults)  # type: ignore[arg-type]


def test_model_y_max_turns_salen_del_agent_request():
    request = _request(model="claude-opus-4-1", max_turns=1)

    options = build_options(request, mcp_servers={}, allowed_tools=[])

    assert options.model == "claude-opus-4-1"
    assert options.max_turns == 1


def test_setting_sources_es_lista_vacia_explicita_nunca_none():
    request = _request()

    options = build_options(request, mcp_servers={}, allowed_tools=[])

    assert options.setting_sources == []
    assert options.setting_sources is not None


def test_strict_mcp_config_es_true():
    request = _request()

    options = build_options(request, mcp_servers={}, allowed_tools=[])

    assert options.strict_mcp_config is True


def test_tools_es_lista_vacia_explicita_nunca_none():
    """`tools=None` (el default del SDK) NO apaga el toolset del CLI.

    Verificado en `_internal/transport/subprocess_cli.py` del paquete
    instalado (`claude-agent-sdk` 0.2.153, citado en el docstring de
    `build_options`): con `tools=None` el CLI no recibe `--tools` y
    conserva Bash/Read/Edit/... por defecto; solo `tools=[]` produce
    `--tools ""` y los apaga de verdad. Con el abstract de un paper
    (texto no confiable) viajando en el prompt, dejar el toolset por
    defecto disponible sería superficie de ataque y turnos/tokens
    gastados sin necesidad -- si esta aserción se pone en verde con
    `options.tools is None`, la regresión ha vuelto sin que nadie lo note.
    """
    request = _request()

    options = build_options(request, mcp_servers={}, allowed_tools=[])

    assert options.tools == []
    assert options.tools is not None


def test_skills_es_lista_vacia_explicita_nunca_none():
    """`skills=[]` explícito, aunque -- a diferencia de `tools` -- con
    `setting_sources=[]` fijo como en `build_options` produce hoy un argv
    byte a byte idéntico a `skills=None` (`_apply_skills_defaults`,
    `subprocess_cli.py:544-559`): no es "el mismo motivo que `tools`", ahí
    `tools=[]` sí cambia el argv real (`--tools ""`) frente al default
    `None`. `skills=[]` se mantiene como defensa en profundidad: si algún
    día se quita el `setting_sources=[]` de `build_options`, `skills=[]`
    (a diferencia de `skills=None`) fuerza `setting_sources=["user",
    "project"]` en `_apply_skills_defaults` -- justo lo que
    `setting_sources=[]` existe para evitar. Ver el docstring de
    `build_options` para el detalle completo.
    """
    request = _request()

    options = build_options(request, mcp_servers={}, allowed_tools=[])

    assert options.skills == []
    assert options.skills is not None


def test_mcp_servers_y_allowed_tools_inyectados_llegan_tal_cual():
    request = _request()
    mcp_servers = {"arxiv": {"type": "sdk", "name": "arxiv"}}
    allowed_tools = ["mcp__arxiv__search"]

    options = build_options(request, mcp_servers=mcp_servers, allowed_tools=allowed_tools)

    assert options.mcp_servers == mcp_servers
    assert options.allowed_tools == allowed_tools


def test_permission_mode_no_es_bypass_permissions():
    request = _request()

    options = build_options(request, mcp_servers={}, allowed_tools=[])

    assert options.permission_mode != "bypassPermissions"
    assert options.permission_mode == "default"


def test_env_no_introduce_credenciales():
    request = _request()

    options = build_options(request, mcp_servers={}, allowed_tools=[])

    assert options.env == {}
