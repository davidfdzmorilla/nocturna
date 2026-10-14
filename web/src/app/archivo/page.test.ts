import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => ({
  fetchArchiveWeeks: vi.fn(),
  fetchArchiveWeek: vi.fn(),
}));

import { fetchArchiveWeeks } from "@/lib/api/client";
import ArchivePage, { metadata } from "./page";

async function render() {
  return renderToStaticMarkup(await ArchivePage());
}

beforeEach(() => vi.mocked(fetchArchiveWeeks).mockReset());

describe("página /archivo", () => {
  it("título de pestaña", () => {
    expect(metadata.title).toBe("Resumen del archivo");
  });

  it("lista: H1, nota de D6, filas enlazadas con recuentos y 'Sin cambios'", async () => {
    vi.mocked(fetchArchiveWeeks).mockResolvedValue({
      ok: true,
      data: {
        weeks: [
          { week: "2026-W41", snapshots: 1, changed: 0, new_planet: 70, regained: 0, lost: 0 },
          { week: "2026-W40", snapshots: 1, changed: 1, new_planet: 3, regained: 0, lost: 2 },
          { week: "2026-W39", snapshots: 1, changed: 0, new_planet: 0, regained: 0, lost: 0 },
        ],
      },
    });
    const html = await render();
    expect(html).toContain("Resumen semanal del NASA Exoplanet Archive");
    expect(html).toContain("esta sección no contiene texto generado por IA.");
    expect(html).toContain('href="/archivo/2026-W41"');
    expect(html).toContain("Semana 41 de 2026 (5–11 de octubre)");
    expect(html).toContain(
      "Referencia cambiada: 0 · Planetas nuevos: 70 · Recuperados: 0 · Pierden la referencia: 0",
    );
    expect(html).toContain(
      "Referencia cambiada: 1 · Planetas nuevos: 3 · Recuperados: 0 · Pierden la referencia: 2",
    );
    expect(html).toContain("Sin cambios");
    expect(html).not.toContain("snapshot");
  });

  it.each(["2026-41", "<b>x</b>"])("semana mal formada %j: sin enlace y texto escapado", async (bad) => {
    vi.mocked(fetchArchiveWeeks).mockResolvedValue({
      ok: true,
      data: { weeks: [{ week: bad, snapshots: 1, changed: 1, new_planet: 0, regained: 0, lost: 0 }] },
    });
    const html = await render();
    expect(html).not.toContain(`/archivo/${bad}`);
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("<b>x</b>");
    expect(html).toContain(bad.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"));
  });

  it("vacía", async () => {
    vi.mocked(fetchArchiveWeeks).mockResolvedValue({ ok: true, data: { weeks: [] } });
    const html = await render();
    expect(html).toContain("Todavía no hay resúmenes semanales.");
    expect(html).toContain("El archivo se consulta los viernes.");
  });

  it("API caída: aviso de error sin detalles", async () => {
    vi.mocked(fetchArchiveWeeks).mockResolvedValue({ ok: false, reason: "unavailable" });
    const html = await render();
    expect(html).toContain('role="alert"');
    expect(html).toContain("No se ha podido cargar el resumen del archivo ahora mismo.");
    expect(html).toContain("Vuelve a intentarlo en unos minutos.");
    expect(html).not.toContain("Todavía no hay");
  });
});
