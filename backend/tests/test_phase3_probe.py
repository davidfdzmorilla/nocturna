"""T110.a: `phase3-probe`, el cliente HTTP de la web publica y el registro de comprobaciones.

Todo contra `httpx.MockTransport`: ninguna peticion real, ninguna base, ningun Claude.
Nombres en espanol.
"""

from __future__ import annotations

import ssl
import stat
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fakes.clock import FakeClock
from helpers.phase3 import FIRST, at

from nocturna import cli
from nocturna.application.use_cases.phase3 import (
    FAILED,
    NOT_CONFIGURED,
    URL_MARKER,
    ProbePublicSite,
    SiteResponse,
)
from nocturna.domain.phase3 import ProbeRecord
from nocturna.infrastructure.ops.phase3_log import FileDeliveryLog, FileProbeLog
from nocturna.infrastructure.ops.public_site import HttpxPublicSite

URL = "https://nocturna.example.org/"
NOW = at(FIRST, 6, 15, 30)
PAGE = (
    "<html><head><title>N</title></head><body><footer>Datos actualizados el "
    '<time dateTime="2026-11-02T03:41:10Z">2 nov</time></footer></body></html>'
)


def _site(handler) -> HttpxPublicSite:
    return HttpxPublicSite(transport=httpx.MockTransport(handler))


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=PAGE, request=request)


# --- cliente HTTP -------------------------------------------------------------------------


def test_http_200_devuelve_estado_html_y_tls_ok():
    result = _site(_ok).fetch(URL)

    assert (result.status, result.tls_ok, result.error_class) == (200, True, None)
    assert "Datos actualizados el" in (result.html or "")


def test_http_503_se_devuelve_como_estado_sin_error():
    result = _site(lambda r: httpx.Response(503, text="caido")).fetch(URL)

    assert (result.status, result.error_class) == (503, None)


def test_un_error_tls_se_anota_como_tls_fallido_con_solo_el_nombre_de_la_clase():
    def handler(request):
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed for nocturna.example.org")
        except ssl.SSLError as inner:
            raise httpx.ConnectError(f"{URL} {inner}") from inner

    result = _site(handler).fetch(URL)

    assert result.status is None and result.tls_ok is False
    # la causa de TLS manda: el nombre de la clase SSL distingue el fallo de TLS
    assert result.error_class == "SSLCertVerificationError"
    assert result.html is None


def test_un_timeout_se_anota_con_el_nombre_de_la_clase():
    def handler(request):
        raise httpx.ReadTimeout(f"timeout hablando con {URL}")

    result = _site(handler).fetch(URL)

    assert (result.status, result.tls_ok, result.error_class) == (None, False, "ReadTimeout")


class _Slow(httpx.SyncByteStream):
    """Cuerpo que entrega un fragmento cada `pause` s, sin terminar nunca."""

    def __init__(self, pause: float) -> None:
        self._pause = pause

    def __iter__(self):
        while True:
            time.sleep(self._pause)
            yield b"x"


def test_un_transporte_lento_que_supera_el_tope_total_da_probetimeout_y_no_se_cuelga():
    site = HttpxPublicSite(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=_Slow(0.05))),
        total_timeout_s=0.5,
    )

    started = time.monotonic()
    result = site.fetch(URL)

    assert time.monotonic() - started < 5
    assert (result.status, result.tls_ok, result.error_class) == (None, False, "ProbeTimeout")
    assert result.html is None


def test_un_cuerpo_por_encima_del_corte_da_probebodytoolarge_con_el_estado():
    site = HttpxPublicSite(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"y" * 5_000)),
        max_bytes=1_000,
    )

    result = site.fetch(URL)

    assert (result.status, result.error_class, result.html) == (200, "ProbeBodyTooLarge", None)
    assert result.tls_ok is True


def test_un_cuerpo_justo_en_el_corte_se_acepta():
    site = HttpxPublicSite(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"y" * 1_000)),
        max_bytes=1_000,
    )

    assert site.fetch(URL).error_class is None


def test_sin_respuesta_y_sin_error_de_tls_tls_es_fail_y_error_lo_distingue():
    def handler(request):
        raise httpx.ConnectError("no se pudo resolver el nombre")  # DNS, no TLS

    result = _site(handler).fetch(URL)

    assert result.tls_ok is False
    assert result.error_class == "ConnectError"  # no es una clase SSL: el fallo no es de TLS


def test_con_respuesta_https_tls_es_ok():
    assert _site(_ok).fetch(URL).tls_ok is True


def test_con_respuesta_http_plana_tls_no_se_da_por_verificado():
    assert _site(_ok).fetch("http://nocturna.example.org/").tls_ok is False


def test_una_redireccion_301_no_se_sigue():
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(301, headers={"location": "https://otro.example.org/"})

    result = _site(handler).fetch(URL)

    assert result.status == 301
    assert calls == [URL]  # una sola peticion, sin ir a la Location


def test_un_esquema_http_no_cuenta_como_tls_verificado():
    result = _site(_ok).fetch("http://nocturna.example.org/")

    assert result.status == 200 and result.tls_ok is False


def test_una_url_invalida_no_lanza():
    result = HttpxPublicSite(transport=httpx.MockTransport(_ok)).fetch("no es una url")

    assert result.status is None and result.error_class


def test_el_cliente_hace_un_solo_intento():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("boom")

    _site(handler).fetch(URL)

    assert len(calls) == 1


def test_el_cliente_usa_10_s_por_fase_sin_redirecciones_y_verificacion_tls_por_defecto(monkeypatch):
    seen: dict = {}
    real = httpx.Client

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(httpx, "Client", spy)

    _site(_ok).fetch(URL)

    assert seen["timeout"] == 10.0  # por fase; el tope total de 30 s lo pone el reloj
    assert HttpxPublicSite()._total_timeout_s == 30.0
    assert seen["follow_redirects"] is False
    assert "verify" not in seen  # almacen de confianza por defecto, sin `-k`


def test_noindex_en_la_cabecera_x_robots_tag_se_anota_como_noindex(tmp_path):
    site = _site(
        lambda r: httpx.Response(200, text=PAGE, headers={"X-Robots-Tag": "noindex, nofollow"})
    )
    log = FileProbeLog(tmp_path)

    ProbePublicSite(site, log, FakeClock(NOW))(URL, "scheduled")

    assert log.read()[0].noindex is True


def _anotado_con_cabeceras(tmp_path, headers) -> bool:
    site = _site(lambda r: httpx.Response(200, text=PAGE, headers=headers))
    log = FileProbeLog(tmp_path)
    ProbePublicSite(site, log, FakeClock(NOW))(URL, "scheduled")
    return log.read()[0].noindex


@pytest.mark.parametrize(
    "headers",
    [
        [("X-Robots-Tag", "NOINDEX, NoFollow")],
        [("x-robots-tag", "none")],
        [("X-Robots-Tag", "nofollow"), ("X-Robots-Tag", "NoIndex")],
        [("X-Robots-Tag", "googlebot: NONE")],
    ],
)
def test_noindex_en_la_cabecera_en_mayusculas_o_repetida_o_none(tmp_path, headers):
    assert _anotado_con_cabeceras(tmp_path, headers) is True


@pytest.mark.parametrize("headers", [[], [("X-Robots-Tag", "nofollow")]])
def test_sin_noindex_en_la_cabecera_no_se_anota(tmp_path, headers):
    assert _anotado_con_cabeceras(tmp_path, headers) is False


# --- caso de uso --------------------------------------------------------------------------


class _Site:
    def __init__(self, response: SiteResponse | None = None) -> None:
        self.calls: list[str] = []
        self._response = response or SiteResponse(200, True, None, PAGE)

    def fetch(self, url: str) -> SiteResponse:
        self.calls.append(url)
        return self._response


class _MemoryLog:
    def __init__(self, fail: bool = False) -> None:
        self.records: list[ProbeRecord] = []
        self.fail = fail

    def append(self, record: ProbeRecord) -> None:
        if self.fail:
            raise PermissionError("no se puede escribir")
        self.records.append(record)

    def read(self) -> list[ProbeRecord]:
        return self.records


@pytest.mark.parametrize("url", [None, "", "   ", URL_MARKER, f"  {URL_MARKER} "])
def test_sin_url_vacia_o_con_el_marcador_sale_78_sin_red_ni_log(url):
    site, log = _Site(), _MemoryLog()

    code = ProbePublicSite(site, log, FakeClock(NOW))(url, "scheduled")

    assert code == NOT_CONFIGURED == 78
    assert site.calls == [] and log.records == []


def test_una_comprobacion_correcta_anota_pie_y_hora_y_sale_0():
    site, log = _Site(), _MemoryLog()

    code = ProbePublicSite(site, log, FakeClock(NOW))(f" {URL} ", "run-night")

    assert code == 0
    assert site.calls == [URL]
    record = log.records[0]
    assert record.checked_at == NOW and record.trigger == "run-night"
    assert record.http_status == 200 and record.tls_ok and not record.noindex
    assert record.footer_updated_at == datetime.fromisoformat("2026-11-02T03:41:10+00:00")


def test_la_web_con_noindex_en_el_meta_se_anota_como_noindex():
    page = '<head><meta name="robots" content="noindex"></head>' + PAGE
    site, log = _Site(SiteResponse(200, True, None, page)), _MemoryLog()

    ProbePublicSite(site, log, FakeClock(NOW))(URL, "scheduled")

    assert log.records[0].noindex is True


def test_un_fallo_del_log_sale_1():
    code = ProbePublicSite(_Site(), _MemoryLog(fail=True), FakeClock(NOW))(URL, "scheduled")

    assert code == FAILED == 1


def test_una_respuesta_con_error_se_anota_igual_y_el_codigo_es_0():
    site = _Site(SiteResponse(None, False, "ConnectError", None))
    log = _MemoryLog()

    code = ProbePublicSite(site, log, FakeClock(NOW))(URL, "scheduled")

    assert code == 0
    assert log.records[0].http_status is None and log.records[0].error_class == "ConnectError"


# --- registro en fichero -------------------------------------------------------------------


def test_el_fichero_de_comprobaciones_queda_en_0600_y_se_anade_al_final(tmp_path):
    log = FileProbeLog(tmp_path / "nuevo")
    probe_use_case = ProbePublicSite(_Site(), log, FakeClock(NOW))

    probe_use_case(URL, "scheduled")
    probe_use_case(URL, "manual")

    path = tmp_path / "nuevo" / "phase3-probes.log"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert len(path.read_text().splitlines()) == 2
    assert [r.trigger for r in log.read()] == ["scheduled", "manual"]


def test_las_lineas_basura_del_registro_se_ignoran_al_leer(tmp_path):
    log = FileProbeLog(tmp_path)
    ProbePublicSite(_Site(), log, FakeClock(NOW))(URL, "scheduled")
    with (tmp_path / "phase3-probes.log").open("a") as handle:
        handle.write("basura\n\n=== otra cosa ===\nchecked_at=ayer\n")

    assert len(log.read()) == 1


def test_leer_registros_que_no_existen_da_listas_vacias(tmp_path):
    assert FileProbeLog(tmp_path).read() == []
    assert FileDeliveryLog(tmp_path).read() == []


def test_el_registro_de_entregas_ignora_basura_y_lee_las_lineas_buenas(tmp_path):
    (tmp_path / "morning-deliveries.log").write_text(
        "basura\n2026-11-05T07:30:00+01:00 2026-11-05 scheduled completed - notified=ok\n"
    )

    records = FileDeliveryLog(tmp_path).read()

    assert len(records) == 1 and records[0].notified == "ok"


def test_el_fichero_de_comprobaciones_no_lleva_url_dsn_entorno_ni_rutas(tmp_path, monkeypatch):
    dsn = "postgresql+psycopg://u:s3cr3t@10.0.0.9:5432/nocturna"
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", dsn)
    monkeypatch.setenv("NOCTURNA_SECRETO", "valor-secreto-de-entorno")
    secret_url = "https://secreto-dominio.example.net/ruta?token=abc"

    def handler(request):
        raise httpx.ConnectError(f"fallo {secret_url} {tmp_path} {dsn}")

    log_dir = tmp_path / "logs"
    log = FileProbeLog(log_dir)
    ProbePublicSite(_site(handler), log, FakeClock(NOW))(secret_url, "scheduled")

    text = (log_dir / "phase3-probes.log").read_text()
    for forbidden in (
        "secreto-dominio",
        "token=abc",
        "s3cr3t",
        "valor-secreto",
        str(tmp_path),
        "/Users",
        "postgresql",
        "https://",
    ):
        assert forbidden not in text
    assert "error=ConnectError" in text
    assert len(text.splitlines()) == 1


# --- CLI ---------------------------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("NOCTURNA_LOG_DIR", str(log_dir))
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/none")

    def no_database(*args, **kwargs):
        raise AssertionError("phase3-probe no debe abrir la base")

    monkeypatch.setattr(cli, "create_db_engine", no_database)
    monkeypatch.setattr(cli, "create_session_factory", no_database)
    monkeypatch.setattr(cli, "unit_of_work", no_database)
    return log_dir


def _fake_site_in_cli(monkeypatch, handler=_ok):
    monkeypatch.setattr(
        cli, "HttpxPublicSite", lambda: HttpxPublicSite(httpx.MockTransport(handler))
    )


class _NoNetwork:
    def fetch(self, url: str):
        raise AssertionError("no debe haber ninguna peticion")


@pytest.mark.parametrize("url", [None, "", URL_MARKER])
def test_cli_sin_url_o_con_el_marcador_sale_78_sin_red_ni_log(cli_env, monkeypatch, url, capsys):
    if url is None:
        monkeypatch.delenv("NOCTURNA_PUBLIC_URL", raising=False)
    else:
        monkeypatch.setenv("NOCTURNA_PUBLIC_URL", url)
    monkeypatch.setattr(cli, "HttpxPublicSite", _NoNetwork)

    code = cli.main(["phase3-probe"])

    assert code == 78
    assert not (cli_env / "phase3-probes.log").exists()
    assert url is None or not url or url not in capsys.readouterr().err


def test_cli_con_url_anota_una_linea_con_el_disparador_y_sale_0(cli_env, monkeypatch):
    monkeypatch.setenv("NOCTURNA_PUBLIC_URL", URL)
    _fake_site_in_cli(monkeypatch)

    code = cli.main(["phase3-probe", "--trigger", "run-night"])

    assert code == 0
    path = cli_env / "phase3-probes.log"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    (line,) = path.read_text().splitlines()
    assert (
        " trigger=run-night http=200 tls=ok error=- noindex=no footer=2026-11-02T03:41:10+00:00"
        in line
    )
    assert URL not in line


def test_cli_el_disparador_por_defecto_es_scheduled(cli_env, monkeypatch):
    monkeypatch.setenv("NOCTURNA_PUBLIC_URL", URL)
    _fake_site_in_cli(monkeypatch)

    cli.main(["phase3-probe"])

    assert " trigger=scheduled " in (cli_env / "phase3-probes.log").read_text()


def test_cli_rechaza_un_disparador_desconocido():
    with pytest.raises(SystemExit) as exc:
        cli.main(["phase3-probe", "--trigger", "cron"])

    assert exc.value.code == 2


def test_cli_un_servidor_caido_se_anota_y_sale_0(cli_env, monkeypatch):
    monkeypatch.setenv("NOCTURNA_PUBLIC_URL", URL)

    def handler(request):
        raise httpx.ConnectTimeout(f"timeout {URL}")

    _fake_site_in_cli(monkeypatch, handler)

    assert cli.main(["phase3-probe"]) == 0
    text = (cli_env / "phase3-probes.log").read_text()
    assert "http=- tls=fail error=ConnectTimeout" in text and URL not in text


def test_cli_si_no_se_puede_anotar_sale_1_y_el_mensaje_no_lleva_la_url(
    tmp_path, monkeypatch, capsys
):
    blocked = tmp_path / "es-un-fichero"
    blocked.write_text("x")
    monkeypatch.setenv("NOCTURNA_LOG_DIR", str(blocked))
    monkeypatch.setenv("NOCTURNA_PUBLIC_URL", URL)
    _fake_site_in_cli(monkeypatch)

    code = cli.main(["phase3-probe"])

    assert code == 1
    # el aviso propio del comando (la ultima linea de stderr) no lleva la URL; el INFO de httpx
    # ("HTTP Request: GET <url>") es del cliente y se queda en el err.log del envoltorio.
    last = capsys.readouterr().err.strip().splitlines()[-1]
    assert last == "phase3-probe: no se pudo anotar la comprobación"


def test_cli_con_nocturna_log_dir_vacio_sale_1(monkeypatch):
    monkeypatch.setenv("NOCTURNA_LOG_DIR", "")
    monkeypatch.setenv("NOCTURNA_PUBLIC_URL", URL)
    monkeypatch.setattr(cli, "HttpxPublicSite", _NoNetwork)

    assert cli.main(["phase3-probe"]) == 1


def test_la_marca_de_tiempo_de_la_comprobacion_lleva_zona_horaria(cli_env, monkeypatch):
    monkeypatch.setenv("NOCTURNA_PUBLIC_URL", URL)
    _fake_site_in_cli(monkeypatch)

    cli.main(["phase3-probe"])

    record = FileProbeLog(cli_env).read()[0]
    assert record.checked_at.tzinfo is not None


def test_el_directorio_de_logs_por_defecto_es_el_home(tmp_path, monkeypatch):
    monkeypatch.delenv("NOCTURNA_LOG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("NOCTURNA_PUBLIC_URL", URL)
    _fake_site_in_cli(monkeypatch)

    assert cli.main(["phase3-probe"]) == 0
    assert (tmp_path / "nocturna-logs" / "phase3-probes.log").is_file()


def test_cli_no_deja_la_url_en_stderr_ni_el_info_de_httpx(cli_env, monkeypatch, capsys):
    monkeypatch.setenv("NOCTURNA_PUBLIC_URL", URL)
    _fake_site_in_cli(monkeypatch)

    code = cli.main(["phase3-probe"])

    captured = capsys.readouterr()
    assert code == 0
    assert URL not in captured.err and "nocturna.example.org" not in captured.err
    assert "HTTP Request" not in captured.err
