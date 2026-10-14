import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  fetchArchiveWeeks: vi.fn(),
  fetchArchiveWeek: vi.fn(),
}));
const notFound = vi.hoisted(() =>
  vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
);
vi.mock("next/navigation", () => ({ notFound }));

import { fetchArchiveWeek } from "@/lib/api/client";
import { week40 } from "@/lib/archiveFixtures";
import ArchiveWeekPage, { generateMetadata } from "./page";

const params = (semana: string) => ({ params: Promise.resolve({ semana }) });

beforeEach(() => {
  vi.mocked(fetchArchiveWeek).mockReset();
  notFound.mockClear();
});

describe("página /archivo/[semana]", () => {
  it("semana inválida: notFound sin llamar a la API", async () => {
    await expect(ArchiveWeekPage(params("2026-W00"))).rejects.toThrow("NEXT_NOT_FOUND");
    expect(notFound).toHaveBeenCalledOnce();
    expect(fetchArchiveWeek).not.toHaveBeenCalled();
  });

  it("not_found de la API: notFound", async () => {
    vi.mocked(fetchArchiveWeek).mockResolvedValue({ ok: false, reason: "not_found" });
    await expect(ArchiveWeekPage(params("2026-W41"))).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("unavailable: aviso, sin notFound", async () => {
    vi.mocked(fetchArchiveWeek).mockResolvedValue({ ok: false, reason: "unavailable" });
    const html = renderToStaticMarkup(await ArchiveWeekPage(params("2026-W41")));
    expect(notFound).not.toHaveBeenCalled();
    expect(html).toContain("No se ha podido cargar el resumen del archivo ahora mismo.");
  });

  it("digest: H1, nota, intro, enlace de vuelta y cuatro secciones", async () => {
    vi.mocked(fetchArchiveWeek).mockResolvedValue({ ok: true, data: week40 });
    const html = renderToStaticMarkup(await ArchiveWeekPage(params("2026-W40")));
    expect(html).toContain("Semana 40 de 2026 (28 de septiembre – 4 de octubre)");
    expect(html).toContain("esta sección no contiene texto generado por IA.");
    expect(html).toContain("es la solución por defecto del archivo");
    expect(html).toContain('href="/archivo"');
    for (const h of ["Referencia cambiada", "Planetas nuevos", "Planetas recuperados", "Planetas que pierden la referencia"]) {
      expect(html).toContain(h);
    }
    expect(fetchArchiveWeek).toHaveBeenCalledWith("2026-W40");
  });

  it("generateMetadata no llama a la API", async () => {
    expect(await generateMetadata(params("2026-W41"))).toEqual({ title: "Semana 41 de 2026" });
    expect(await generateMetadata(params("zzz"))).toEqual({ title: "Resumen del archivo" });
    expect(fetchArchiveWeek).not.toHaveBeenCalled();
  });
});
