import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  fetchFindingsPage: vi.fn(),
}));

import { fetchFindingsPage } from "@/lib/api/client";
import Home from "./page";

async function render(searchParams: { page?: string; tipo?: string }) {
  return renderToStaticMarkup(
    await Home({ searchParams: Promise.resolve(searchParams) }),
  );
}

beforeEach(() => vi.mocked(fetchFindingsPage).mockReset());

describe("feed con ?tipo=", () => {
  it("tension-soluciones pide solution_tension; total 0 -> aviso", async () => {
    vi.mocked(fetchFindingsPage).mockResolvedValue({
      ok: true,
      data: { items: [], page: 1, size: 20, total: 0 },
    });
    const html = await render({ tipo: "tension-soluciones" });
    expect(fetchFindingsPage).toHaveBeenCalledWith(
      1,
      expect.any(Number),
      "solution_tension",
    );
    expect(html).toContain("No hay hallazgos de este tipo");
  });

  it("el filtro enlaza las seis opciones", async () => {
    vi.mocked(fetchFindingsPage).mockResolvedValue({
      ok: true,
      data: { items: [], page: 1, size: 20, total: 0 },
    });
    const html = await render({ tipo: "tension-soluciones" });
    for (const href of [
      'href="/"',
      'href="/?tipo=articulo"',
      'href="/?tipo=tension"',
      'href="/?tipo=tension-soluciones"',
      'href="/?tipo=primera-medida"',
      'href="/?tipo=confirmacion"',
    ]) {
      expect(html).toContain(href);
    }
    expect(html).toContain("Tensión entre soluciones");
    expect(html).toContain('aria-current="page"');
  });

  it("tipo inválido no filtra", async () => {
    vi.mocked(fetchFindingsPage).mockResolvedValue({
      ok: true,
      data: { items: [], page: 1, size: 20, total: 0 },
    });
    await render({ tipo: "solution_tension" });
    expect(fetchFindingsPage).toHaveBeenCalledWith(1, expect.any(Number), null);
  });
});
