"""Tests de `application/use_cases/list_findings.py` (T50 paso 2).

Sin base de datos: repositorios falsos locales, mínimos para lo que estos
dos casos de uso necesitan (`testing-without-claude`). El equivalente contra
PostgreSQL real de los tres métodos de `FindingRepository` que aquí se
falsean vive en `tests/db/test_finding_repository.py` (T50 paso 1); este
módulo no repite esas pruebas, solo `ListPublishedFindings`/
`GetPublishedFinding` por encima.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from nocturna.application.use_cases.list_findings import (
    FindingsPage,
    GetPublishedFinding,
    ListPublishedFindings,
    PublishedFinding,
)
from nocturna.domain.entities import Finding, FindingType, Item, ItemStatus

_PUBLISHED_AT = datetime(2026, 1, 2, 3, 0, 0, tzinfo=UTC)


def _make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "source": "arxiv",
        "external_id": "2601.00001",
        "title": "A test paper",
        "abstract": "An abstract.",
        "categories": ["astro-ph.EP"],
        "published_at": _PUBLISHED_AT,
        "fetched_at": _PUBLISHED_AT,
        "status": ItemStatus.NEW,
    }
    defaults.update(overrides)
    return Item(**defaults)


def _make_finding(*, item_id: UUID, published: bool = True, **overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "item_id": item_id,
        "run_id": uuid4(),
        "type": FindingType.PAPER_EXPLAINED,
        "title": "Un hallazgo de prueba",
        "level_curious": "Nivel curioso.",
        "level_amateur": "Nivel aficionado.",
        "level_technical": "Nivel técnico.",
    }
    defaults.update(overrides)
    finding = Finding(**defaults)
    if published:
        finding.publish(confidence=0.8, at=_PUBLISHED_AT)
    return finding


class _FakeFindingRepository:
    """Doble mínimo de `FindingRepository`: solo los tres métodos de T50 paso 1.

    Permite construirse con hallazgos que la "página" o el "detalle"
    devuelven SIN filtrar por publicación, precisamente para poder probar
    que el caso de uso -- no el repositorio -- es quien descarta lo que no
    está publicado (la segunda barrera del docstring del módulo).
    """

    def __init__(
        self,
        *,
        page: list[Finding] | None = None,
        total: int = 0,
        by_id: dict[UUID, Finding] | None = None,
    ) -> None:
        self._page = page if page is not None else []
        self._total = total
        self._by_id = by_id if by_id is not None else {}

    def add(self, finding: Finding) -> None:
        raise NotImplementedError

    def unpublished_for_run(self, run_id: UUID) -> list[Finding]:
        raise NotImplementedError

    def save(self, finding: Finding) -> None:
        raise NotImplementedError

    def published_page(self, limit: int, offset: int) -> list[Finding]:
        return self._page

    def count_published(self) -> int:
        return self._total

    def get_published(self, finding_id: UUID) -> Finding | None:
        return self._by_id.get(finding_id)


class _FakeItemRepository:
    """Doble mínimo de `ItemRepository`: solo `get`, lo único que usa `GetPublishedFinding`."""

    def __init__(self, *items: Item) -> None:
        self._items: dict[UUID, Item] = {item.id: item for item in items}

    def add_many(self, items: list[Item]) -> int:
        raise NotImplementedError

    def get(self, item_id: UUID) -> Item | None:
        return self._items.get(item_id)

    def next_unread(self, limit: int) -> list[Item]:
        raise NotImplementedError

    def save(self, item: Item) -> None:
        raise NotImplementedError


def test_list_published_findings_propaga_limit_offset_y_total() -> None:
    item = _make_item()
    findings = [_make_finding(item_id=item.id), _make_finding(item_id=item.id)]
    repo = _FakeFindingRepository(page=findings, total=57)
    use_case = ListPublishedFindings(repo)

    result = use_case(limit=2, offset=10)

    assert result == FindingsPage(findings=findings, total=57)


def test_list_published_findings_descarta_entidad_no_publicada_del_repositorio() -> None:
    """Segunda barrera: aunque el repositorio (por error) devuelva un
    `Finding` sin `published_at` dentro de la página, el caso de uso no lo
    propaga.
    """
    item = _make_item()
    published = _make_finding(item_id=item.id)
    unpublished = _make_finding(item_id=item.id, published=False)
    repo = _FakeFindingRepository(page=[published, unpublished], total=2)
    use_case = ListPublishedFindings(repo)

    result = use_case(limit=10, offset=0)

    assert result.findings == [published]
    # `total` no se recalcula a partir de lo filtrado: viene de `count_published()`.
    assert result.total == 2


def test_get_published_finding_devuelve_none_si_el_repositorio_no_tiene_el_id() -> None:
    repo = _FakeFindingRepository(by_id={})
    items = _FakeItemRepository()
    use_case = GetPublishedFinding(repo, items)

    assert use_case(uuid4()) is None


def test_get_published_finding_devuelve_none_para_un_finding_sin_published_at() -> None:
    """Segunda barrera: si `get_published` (por error) devolviera un
    `Finding` sin `published_at`, el caso de uso lo trata como inexistente.
    """
    item = _make_item()
    unpublished = _make_finding(item_id=item.id, published=False)
    repo = _FakeFindingRepository(by_id={unpublished.id: unpublished})
    items = _FakeItemRepository(item)
    use_case = GetPublishedFinding(repo, items)

    assert use_case(unpublished.id) is None


def test_get_published_finding_compone_source_y_external_id_desde_el_item() -> None:
    item = _make_item(source="arxiv", external_id="2601.12345")
    finding = _make_finding(item_id=item.id)
    repo = _FakeFindingRepository(by_id={finding.id: finding})
    items = _FakeItemRepository(item)
    use_case = GetPublishedFinding(repo, items)

    result = use_case(finding.id)

    assert result == PublishedFinding(finding=finding, source="arxiv", external_id="2601.12345")


def test_get_published_finding_devuelve_none_si_el_item_no_existe() -> None:
    """Decisión documentada en el caso de uso: sin `Item`, no hay
    `source`/`external_id` que componer, así que se falla cerrado en vez de
    inventar un enlace.
    """
    item = _make_item()
    finding = _make_finding(item_id=item.id)
    repo = _FakeFindingRepository(by_id={finding.id: finding})
    items = _FakeItemRepository()  # vacío a propósito: el `Item` "no existe"

    use_case = GetPublishedFinding(repo, items)

    assert use_case(finding.id) is None
