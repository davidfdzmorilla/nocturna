import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  fetchFindingsPage: vi.fn(),
  fetchArchiveWeeks: vi.fn(),
  fetchArchiveWeek: vi.fn(),
}));

import {
  fetchArchiveWeek,
  fetchArchiveWeeks,
  fetchFindingsPage,
} from "@/lib/api/client";
import { week40, week42WithText } from "@/lib/archiveFixtures";
import type { FindingsPage } from "@/lib/api/types";
import { HOME_AI_TEXT_LEGEND, HOME_AI_TEXT_NOTE } from "@/lib/archiveDigest";
import Home from "./page";

async function render(searchParams: { page?: string; tipo?: string }) {
  return renderToStaticMarkup(
    await Home({ searchParams: Promise.resolve(searchParams) }),
  );
}

const WEEKS = {
  ok: true as const,
  data: {
    weeks: [
      {
        week: "2026-W43",
        snapshots: 1,
        changed: 0,
        new_planet: 0,
        regained: 0,
        lost: 0,
      },
      {
        week: "2026-W42",
        snapshots: 1,
        changed: 0,
        new_planet: 1,
        regained: 0,
        lost: 0,
      },
    ],
  },
};

const FINDINGS: { ok: true; data: FindingsPage } = {
  ok: true,
  data: {
    items: [
      {
        id: "f1",
        type: "paper_explained",
        title: "Hallazgo de prueba",
        published_at: "2026-11-03T03:00:00Z",
        level_curious: "Texto curioso.",
      },
    ],
    page: 1,
    size: 20,
    total: 41,
  },
};

beforeEach(() => {
  vi.mocked(fetchFindingsPage).mockReset();
  vi.mocked(fetchArchiveWeeks).mockReset();
  vi.mocked(fetchArchiveWeek).mockReset();
  vi.mocked(fetchArchiveWeeks).mockResolvedValue(WEEKS);
  vi.mocked(fetchArchiveWeek).mockResolvedValue({
    ok: true,
    data: week42WithText,
  });
  vi.mocked(fetchFindingsPage).mockResolvedValue(FINDINGS);
});

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

describe("portada: resumen semanal y hallazgos", () => {
  const DIGEST_H1 = "Resumen semanal del NASA Exoplanet Archive";

  it("el resumen va antes que «Últimos hallazgos»", async () => {
    const html = await render({});
    expect(html.indexOf(DIGEST_H1)).toBeGreaterThanOrEqual(0);
    expect(html.indexOf(DIGEST_H1)).toBeLessThan(
      html.indexOf("Últimos hallazgos"),
    );
  });

  it("pide el detalle de la semana más reciente con cambios", async () => {
    await render({});
    expect(fetchArchiveWeek).toHaveBeenCalledTimes(1);
    expect(fetchArchiveWeek).toHaveBeenCalledWith("2026-W42");
  });

  it("con texto: etiqueta, título y leyenda de IA; enlaces", async () => {
    const html = await render({});
    expect(html).toContain("Resumen redactado por IA");
    expect(html).toContain("Una semana tranquila en el archivo");
    expect(html).toContain("Ninguna persona lo ha revisado");
    expect(html).toContain('href="/archivo/2026-W42"');
    expect(html).toContain('href="/archivo"');
    expect(html).toContain("Ver la semana completa");
    expect(html).toContain("Todas las semanas");
    expect(html).not.toContain("las tablas no contienen");
    expect(html).not.toContain("el detalle de la semana no contienen");
  });

  it("con texto: nota y leyenda exactas de portada, sin «tablas de abajo»", async () => {
    const html = await render({});
    expect(HOME_AI_TEXT_NOTE).toBe(
      "Los recuentos salen del NASA Exoplanet Archive con plantillas fijas, sin texto generado por IA.",
    );
    expect(HOME_AI_TEXT_LEGEND).toBe(
      "Texto generado automáticamente a partir de los cambios de la semana. Ninguna persona lo ha revisado; los datos de referencia están en la semana completa.",
    );
    expect(html).toContain(HOME_AI_TEXT_NOTE);
    expect(html).toContain(HOME_AI_TEXT_LEGEND);
    expect(html).not.toContain("tablas de abajo");
  });

  it("sin texto: sin etiqueta ni leyenda de IA y con la nota", async () => {
    vi.mocked(fetchArchiveWeek).mockResolvedValue({ ok: true, data: week40 });
    const html = await render({});
    expect(html).not.toContain("Resumen redactado por IA");
    expect(html).not.toContain("Ninguna persona lo ha revisado");
    expect(html).toContain("esta sección no contiene texto generado por IA");
  });

  it("archivo unavailable: aviso y el feed sigue visible", async () => {
    vi.mocked(fetchArchiveWeeks).mockResolvedValue({
      ok: false,
      reason: "unavailable",
    });
    const html = await render({});
    expect(html).toContain("No se ha podido cargar el resumen del archivo");
    expect(html).toContain("Hallazgo de prueba");
    expect(fetchArchiveWeek).not.toHaveBeenCalled();
  });

  it("hallazgos unavailable: aviso y el resumen sigue visible", async () => {
    vi.mocked(fetchFindingsPage).mockResolvedValue({
      ok: false,
      reason: "unavailable",
    });
    const html = await render({});
    expect(html).toContain("No se han podido cargar los hallazgos");
    expect(html).toContain("Una semana tranquila en el archivo");
  });

  it("sin semana con cambios: aviso con enlace y sin pedir el detalle", async () => {
    vi.mocked(fetchArchiveWeeks).mockResolvedValue({
      ok: true,
      data: { weeks: [WEEKS.data.weeks[0]] },
    });
    const html = await render({});
    expect(html).toContain("Todavía no hay semanas con cambios");
    expect(html).toContain('href="/archivo"');
    expect(fetchArchiveWeek).not.toHaveBeenCalled();
    expect(html).toContain("Hallazgo de prueba");
  });

  it("?page=2 pide la página 2", async () => {
    await render({ page: "2" });
    expect(fetchFindingsPage).toHaveBeenCalledWith(2, expect.any(Number), null);
  });

  it("?page=2 pinta la paginación en la página 2", async () => {
    const html = await render({ page: "2" });
    expect(html).toContain("Página 2 de 3");
    expect(html).toContain('href="/?page=3"');
    expect(html).toContain("Una semana tranquila en el archivo");
  });

  it("?tipo= se conserva en la paginación y el resumen sigue arriba", async () => {
    const html = await render({ tipo: "tension" });
    expect(fetchFindingsPage).toHaveBeenCalledWith(
      1,
      expect.any(Number),
      "catalog_tension",
    );
    expect(html).toContain("tipo=tension");
    expect(html.indexOf("Una semana tranquila")).toBeLessThan(
      html.indexOf("Últimos hallazgos"),
    );
  });

  it.each(["unavailable", "not_found"] as const)(
    "detalle %s: el resumen sigue sin tumbarse ni etiqueta de IA",
    async (reason) => {
      vi.mocked(fetchArchiveWeek).mockResolvedValue({ ok: false, reason });
      const html = await render({});
      expect(html).toContain("Ver la semana completa");
      expect(html).not.toContain("Resumen redactado por IA");
      expect(html).toContain("Hallazgo de prueba");
    },
  );

  it("un solo h1 y los encabezados bajan en orden", async () => {
    const html = await render({});
    expect(html.match(/<h1[\s>]/g)).toHaveLength(1);
    const levels = [...html.matchAll(/<h([1-6])[\s>]/g)].map((m) =>
      Number(m[1]),
    );
    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
    expect(levels).toContain(3);
    expect(html).toContain('id="hallazgos"');
  });

  it.each([
    ["sin texto", async () => {
      vi.mocked(fetchArchiveWeek).mockResolvedValue({ ok: true, data: week40 });
    }],
    ["archivo caído", async () => {
      vi.mocked(fetchArchiveWeeks).mockResolvedValue({
        ok: false,
        reason: "unavailable",
      });
    }],
  ])("%s: un solo h1 (sr-only) y encabezados en orden", async (_n, setup) => {
    await setup();
    const html = await render({});
    expect(html.match(/<h1[\s>]/g)).toHaveLength(1);
    if (_n === "archivo caído") expect(html).toMatch(/<h1 class="sr-only">/);
    const levels = [...html.matchAll(/<h([1-6])[\s>]/g)].map((m) =>
      Number(m[1]),
    );
    expect(levels[0]).toBe(1);
    for (let i = 1; i < levels.length; i++) {
      expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    }
  });
});
