import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  fetchFinding: vi.fn(),
}));

import { fetchFinding } from "@/lib/api/client";
import { HD_202206_C_MASS, detailWith, retractedDetail } from "@/lib/findingFixtures";
import FindingPage, { generateMetadata } from "./page";

const ID = "1a2b3c4d-1111-2222-3333-444455556666";

async function render(searchParams: { nivel?: string } = {}) {
  return renderToStaticMarkup(
    await FindingPage({
      params: Promise.resolve({ id: ID }),
      searchParams: Promise.resolve(searchParams),
    }),
  );
}

beforeEach(() => {
  vi.mocked(fetchFinding).mockReset();
  vi.mocked(fetchFinding).mockResolvedValue({
    ok: true,
    data: detailWith(HD_202206_C_MASS),
  });
});

describe("detalle de solution_tension", () => {
  it("sin enlace a arXiv, con nota de origen, etiqueta, chip y bloque", async () => {
    const html = await render();
    expect(html).not.toContain("Ver el artículo original en arXiv");
    expect(html).toContain(
      "Este hallazgo no procede de un artículo de arXiv: compara dos soluciones publicadas en el NASA Exoplanet Archive.",
    );
    expect(html).toContain("Tensión entre soluciones");
    expect(html).toContain("Candidato");
    expect(html).toContain("Datos del contraste");
    expect(html).toContain("982,09 ± 44,5 M⊕");
  });

  it("?nivel=technical sigue mostrando el bloque", async () => {
    const html = await render({ nivel: "technical" });
    expect(html).toContain("Datos del contraste");
    expect(html).toContain("8,20 σ");
    expect(html).toContain("Este hallazgo no procede de un artículo de arXiv");
  });

  it("otros tipos no llevan la nota de origen", async () => {
    vi.mocked(fetchFinding).mockResolvedValue({
      ok: true,
      data: {
        ...detailWith(HD_202206_C_MASS),
        type: "paper_explained",
        solution_tension: null,
        source_url: "https://arxiv.org/abs/2610.01234",
      },
    });
    const html = await render();
    expect(html).not.toContain("no procede de un artículo de arXiv");
    expect(html).toContain("Ver el artículo original en arXiv");
  });
});

const M12 =
  "Retirado: la cifra es una masa mínima (m sin i) de un candidato sin designación; no es la masa de un planeta TOI-2211 c.";
const LEYENDA =
  "Este hallazgo ya no aparece en el feed. Su texto original se conserva debajo, sin cambios.";

function mockRetracted(note: string | null, extra: object = {}) {
  vi.mocked(fetchFinding).mockResolvedValue({
    ok: true,
    data: { ...retractedDetail(note ?? ""), retraction_note: note, ...extra },
  });
}

describe("detalle de hallazgo retirado (T100)", () => {
  it("sin retirada no hay aviso ni leyenda y el resto se mantiene", async () => {
    const html = await render();
    expect(html).not.toContain("Retirado el");
    expect(html).not.toContain('id="retirada"');
    expect(html).not.toContain(LEYENDA);
    expect(html).toContain("Datos del contraste");
    expect(html).toContain("Candidato");
  });

  it("con retirada muestra fecha en Madrid, nota exacta y leyenda", async () => {
    mockRetracted(M12);
    const html = await render();
    expect(html).toContain(
      'Retirado el <time dateTime="2026-11-03T23:30:00Z">4 de noviembre de 2026</time>',
    );
    expect(html).toContain('id="retirada"');
    expect(html).toContain(M12);
    expect(html).toContain(LEYENDA);
  });

  it("el aviso va tras el h1 y antes de la etiqueta de tipo y la fecha de publicación", async () => {
    mockRetracted(M12);
    const html = await render();
    const aviso = html.indexOf('<section aria-labelledby="retirada"');
    expect(aviso).toBeGreaterThan(html.indexOf("</h1>"));
    expect(aviso).toBeLessThan(html.indexOf("Primera medida"));
    expect(aviso).toBeLessThan(
      html.indexOf('<time dateTime="2026-10-02T03:00:00Z"'),
    );
  });

  it.each(["technical", "amateur"])("?nivel=%s mantiene el aviso", async (nivel) => {
    mockRetracted(M12);
    const html = await render({ nivel });
    expect(html).toContain("Retirado el");
    expect(html).toContain(M12);
    expect(html).toContain(LEYENDA);
  });

  it("escapa el HTML de la nota", async () => {
    mockRetracted(`<script>alert(1)</script> & "x" 'y' «z»`);
    const html = await render();
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt; &amp; ");
    expect(html).toContain("«z»");
  });

  it("una nota con saltos de línea es un solo párrafo pre-line, sin br", async () => {
    mockRetracted("Retirado: uno\ndos");
    const html = await render();
    expect(html).toContain('whitespace-pre-line">Retirado: uno\ndos</p>');
    expect(html).not.toContain("<br");
  });

  it("retracted_at sin nota: encabezado y leyenda, sin párrafo de nota", async () => {
    mockRetracted(null);
    const html = await render();
    const aviso = html.slice(
      html.indexOf('<section aria-labelledby="retirada"'),
      html.indexOf("</section>"),
    );
    expect(aviso).toContain("Retirado el");
    expect(aviso).toContain(LEYENDA);
    expect((aviso.match(/<p /g) ?? []).length).toBe(1);
  });

  it("una fecha inválida sale tal cual", async () => {
    mockRetracted(M12, { retracted_at: "no-es-fecha" });
    const html = await render();
    expect(html).toContain(
      'Retirado el <time dateTime="no-es-fecha">no-es-fecha</time>',
    );
  });

  it("un catalog_tension retirado conserva Candidato y Datos del contraste bajo el aviso", async () => {
    mockRetracted(M12, {
      type: "catalog_tension",
      first_measurement: null,
      catalog_tension: {
        planet_name: "X b",
        parameter: "mass",
        archive_url: "https://exoplanetarchive.ipac.caltech.edu/overview/X%20b",
        threshold_sigma: 3,
        reference_sigma: 3.37,
        comparisons: [],
      },
    });
    const html = await render();
    const aviso = html.indexOf('id="retirada"');
    expect(aviso).toBeGreaterThan(-1);
    expect(html.indexOf("Candidato")).toBeGreaterThan(aviso);
    expect(html.indexOf("Datos del contraste")).toBeGreaterThan(aviso);
  });
});

describe("generateMetadata", () => {
  const meta = () => generateMetadata({
    params: Promise.resolve({ id: ID }),
    searchParams: Promise.resolve({}),
  });

  it("éxito: título del hallazgo y sin robots", async () => {
    const m = await meta();
    expect(m.title).toBe(detailWith(HD_202206_C_MASS).title);
    expect(m.robots).toBeUndefined();
  });

  it("not_found: sin noindex", async () => {
    vi.mocked(fetchFinding).mockResolvedValue({ ok: false, reason: "not_found" });
    expect(await meta()).toEqual({ title: "Hallazgo" });
  });

  it("unavailable: noindex, nofollow", async () => {
    vi.mocked(fetchFinding).mockResolvedValue({ ok: false, reason: "unavailable" });
    expect(await meta()).toEqual({
      title: "Hallazgo",
      robots: { index: false, follow: false },
    });
  });
});
