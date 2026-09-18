"""Casos de uso de lectura: hallazgos publicados, para la API de solo lectura (T50).

`ListPublishedFindings` (listado paginado) y `GetPublishedFinding` (detalle)
son los dos únicos puntos por los que la capa HTTP (T50 paso 3) puede leer
un `Finding`. Ninguno de los dos decide `limit`/`offset`: la traducción de
`page`/`size` de la petición HTTP a esos dos enteros es cosa de `api/`, no
de aquí (`CLAUDE.md`: "sin paginación reinventada").

## La segunda barrera es deliberada, no redundancia ociosa

`FindingRepository.published_page`/`get_published` ya filtran
`published_at IS NOT NULL` dentro de la sentencia SQL (T50 paso 1). Estos
dos casos de uso vuelven a comprobar `finding.is_published` sobre la
entidad ya en memoria, **antes** de devolverla. Si un repositorio futuro
cambiara esa consulta y se le escapara el filtro -- un `WHERE` mal
reescrito, un `JOIN` que lo pierde, una implementación alternativa de
`FindingRepository` que lo olvide --, la única cosa que se publica sin que
el Editor la haya aprobado es exactamente lo que este proyecto no puede
permitirse: un hallazgo a medio escribir, sin `confidence`, filtrando a la
web. La comprobación en memoria no depende de que la sentencia SQL esté
bien escrita hoy; es la última línea de defensa si deja de estarlo. No la
"simplifiques" quitándola con el argumento de que el repositorio ya filtra:
ese es precisamente el escenario que existe para cubrir.
"""

from dataclasses import dataclass
from uuid import UUID

from nocturna.domain.entities import Finding
from nocturna.domain.repositories import FindingRepository, ItemRepository


@dataclass(frozen=True, slots=True)
class FindingsPage:
    """Una página de hallazgos publicados, con el total para paginar."""

    findings: list[Finding]
    total: int


@dataclass(frozen=True, slots=True)
class PublishedFinding:
    """Un hallazgo publicado junto con el origen de su `Item`.

    `source`/`external_id` viajan aquí, no en `Finding`, porque el enlace
    al arXiv original vive en el `Item` (`domain/entities.py`): un
    `Finding` no duplica esos campos.
    """

    finding: Finding
    source: str
    external_id: str


class ListPublishedFindings:
    """Página de hallazgos publicados para el feed de la web (`/`).

    Depende solo del `Protocol` `FindingRepository`: se prueba con un
    repositorio falso, sin base de datos (`testing-without-claude`).
    """

    def __init__(self, findings: FindingRepository) -> None:
        self._findings = findings

    def __call__(self, limit: int, offset: int) -> FindingsPage:
        """Devuelve hasta `limit` hallazgos publicados a partir de `offset`.

        `total` viene de `count_published()`, no de `len(findings)`: son
        preguntas distintas (el tamaño de la página frente al total para
        calcular cuántas páginas hay) y coincidir solo por casualidad en la
        última página incompleta.

        Segunda barrera (ver docstring del módulo): cualquier fila que
        `published_page` devolviera sin `published_at` se descarta aquí, no
        se propaga. Descartar en vez de fallar: un listado no debe romperse
        entero por una fila mal filtrada, tiene sentido servir el resto.
        """
        page = self._findings.published_page(limit, offset)
        total = self._findings.count_published()
        published = [finding for finding in page if finding.is_published]
        return FindingsPage(findings=published, total=total)


class GetPublishedFinding:
    """Detalle de un hallazgo publicado, con el origen de su `Item` (`/hallazgo/[id]`).

    Depende solo de los `Protocol` `FindingRepository`/`ItemRepository`.
    """

    def __init__(self, findings: FindingRepository, items: ItemRepository) -> None:
        self._findings = findings
        self._items = items

    def __call__(self, finding_id: UUID) -> PublishedFinding | None:
        """Devuelve el hallazgo publicado con `source`/`external_id`, o `None`.

        Segunda barrera (ver docstring del módulo): si `get_published`
        devolviera una fila sin `published_at`, este caso de uso la trata
        como si no existiera y devuelve `None`, igual que si el id no
        estuviera en absoluto -- nunca la propaga como si fuera un hallazgo
        válido.

        Decisión sobre un `Item` inexistente: `finding.item_id` referencia
        `items.id` con una FK (T11), así que un `Finding` publicado sin su
        `Item` no debería poder ocurrir nunca en la base de datos real. Aun
        así, si `items.get` devolviera `None` -- un repositorio falso mal
        construido en un test, o una futura implementación que lo permita
        --, este caso de uso falla cerrado: devuelve `None` en vez de un
        `PublishedFinding` con `source`/`external_id` inventados o vacíos.
        Es la misma filosofía que el resto del proyecto (el Editor, por
        ejemplo, descarta antes que alucinar): mejor un 404 en la web que
        un enlace a arXiv roto o falso.
        """
        finding = self._findings.get_published(finding_id)
        if finding is None or not finding.is_published:
            return None
        item = self._items.get(finding.item_id)
        if item is None:
            return None
        return PublishedFinding(finding=finding, source=item.source, external_id=item.external_id)
