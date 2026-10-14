import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  fetchFinding: vi.fn(),
}));

import { fetchFinding } from "@/lib/api/client";
import { HD_202206_C_MASS, detailWith } from "@/lib/findingFixtures";
import FindingPage from "./page";

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
