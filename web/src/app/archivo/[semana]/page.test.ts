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
import { week40, week42WithText } from "@/lib/archiveFixtures";
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

  it("sin writer_text: ni etiqueta de IA ni leyenda, y nota AI_FREE_NOTE", async () => {
    vi.mocked(fetchArchiveWeek).mockResolvedValue({ ok: true, data: week40 });
    const html = renderToStaticMarkup(await ArchiveWeekPage(params("2026-W40")));
    expect(html).not.toContain("Resumen redactado por IA");
    expect(html).not.toContain('id="resumen-ia"');
    expect(html).not.toContain("Ninguna persona lo ha revisado");
    expect(html).toContain(
      "Datos del NASA Exoplanet Archive presentados con plantillas fijas; esta sección no contiene texto generado por IA.",
    );
  });

  it("con writer_text: bloque tras el H1 y antes de nota, intro y secciones", async () => {
    vi.mocked(fetchArchiveWeek).mockResolvedValue({ ok: true, data: week42WithText });
    const html = renderToStaticMarkup(await ArchiveWeekPage(params("2026-W42")));
    const at = (s: string) => html.indexOf(s);
    const h1 = at("<h1");
    const block = at("Resumen redactado por IA");
    const note = at("las tablas no contienen texto generado por IA.");
    const intro = at("es la solución por defecto del archivo");
    const firstSection = at("Planetas nuevos");
    expect(block).toBeGreaterThan(h1);
    expect(h1).toBeGreaterThan(-1);
    expect(note).toBeGreaterThan(block);
    expect(intro).toBeGreaterThan(block);
    expect(firstSection).toBeGreaterThan(block);
    expect(html).toContain("Una semana tranquila en el archivo");
    expect(html).toContain("Ninguna persona lo ha revisado");
    expect(html).not.toContain("no contiene texto generado por IA");
  });

  it("con writer_text y sin entradas: el bloque se muestra igualmente", async () => {
    vi.mocked(fetchArchiveWeek).mockResolvedValue({
      ok: true,
      data: { ...week42WithText, entries: [] },
    });
    const html = renderToStaticMarkup(await ArchiveWeekPage(params("2026-W42")));
    expect(html).toContain("Resumen redactado por IA");
    expect(html).toContain("Una semana tranquila en el archivo");
  });

  it("generateMetadata no llama a la API", async () => {
    expect(await generateMetadata(params("2026-W41"))).toEqual({ title: "Semana 41 de 2026" });
    expect(await generateMetadata(params("zzz"))).toEqual({ title: "Resumen del archivo" });
    expect(fetchArchiveWeek).not.toHaveBeenCalled();
  });
});
