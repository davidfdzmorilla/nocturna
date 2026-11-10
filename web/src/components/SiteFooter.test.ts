import { createElement } from "react";
import { renderToReadableStream } from "react-dom/server.edge";
import { beforeEach, describe, expect, it, vi } from "vitest";

// T108: el pie lee `/data-status` con `fetchDataStatus`; aquí se sustituye.
const fetchDataStatus = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/client", () => ({ fetchDataStatus }));
// El layout importa la hoja de estilos (Tailwind/PostCSS); aquí no hace falta.
vi.mock("@/app/globals.css", () => ({}));

import { metadata } from "@/app/layout";
import { SiteFooter } from "./SiteFooter";

async function render(): Promise<string> {
  const stream = await renderToReadableStream(createElement(SiteFooter));
  await stream.allReady;
  return (await new Response(stream).text()).replace(/\s+/g, " ");
}

beforeEach(() => {
  fetchDataStatus.mockReset();
});

describe("SiteFooter: fecha de actualización", () => {
  it("23:30 UTC del 8 de noviembre se muestra el 9 (Europe/Madrid) con <time dateTime>", async () => {
    fetchDataStatus.mockResolvedValue({
      ok: true,
      data: { data_updated_at: "2026-11-08T23:30:00Z" },
    });
    const html = await render();
    expect(html).toContain("Datos actualizados el");
    expect(html).toContain("9 de noviembre de 2026");
    expect(html).not.toContain("8 de noviembre de 2026");
    expect(html).toMatch(/<time datetime="2026-11-08T23:30:00Z">9 de noviembre de 2026 a las 0:30<\/time>/i);
    expect(html).not.toContain("Fecha de actualización no disponible");
  });

  it("en horario de verano la hora es la de Madrid", async () => {
    fetchDataStatus.mockResolvedValue({
      ok: true,
      data: { data_updated_at: "2026-07-01T22:30:00Z" },
    });
    const html = await render();
    expect(html).toContain("2 de julio de 2026");
  });

  it.each([
    ["data_updated_at null", { ok: true, data: { data_updated_at: null } }],
    ["API no disponible", { ok: false, reason: "unavailable" }],
    ["404", { ok: false, reason: "not_found" }],
    ["fecha ilegible", { ok: true, data: { data_updated_at: "no-es-fecha" } }],
    ["cuerpo vacío", { ok: true, data: null }],
    ["fecha no textual", { ok: true, data: { data_updated_at: 12345 } }],
  ])("%s: texto de reserva, sin <time>", async (_n, result) => {
    fetchDataStatus.mockResolvedValue(result);
    const html = await render();
    expect(html).toContain("Fecha de actualización no disponible");
    expect(html).not.toContain("<time");
    expect(html).not.toContain("Datos actualizados el");
  });

  it("si fetchDataStatus lanza, no lanza y muestra el texto de reserva", async () => {
    fetchDataStatus.mockRejectedValue(new Error("boom"));
    const html = await render();
    expect(html).toContain("Fecha de actualización no disponible");
  });

  it("la API caída no pone noindex ni robots en el pie ni en el layout", async () => {
    fetchDataStatus.mockResolvedValue({ ok: false, reason: "unavailable" });
    const html = await render();
    expect(html.toLowerCase()).not.toContain("noindex");
    expect(html.toLowerCase()).not.toContain("<meta");
    expect(metadata.robots).toBeUndefined();
  });
});

describe("SiteFooter: atribución", () => {
  beforeEach(() => {
    fetchDataStatus.mockResolvedValue({ ok: false, reason: "unavailable" });
  });

  it("arXiv bajo CC0 1.0 con enlace a la licencia", async () => {
    const html = await render();
    expect(html).toContain(
      '<a href="https://creativecommons.org/publicdomain/zero/1.0/">CC0 1.0</a>',
    );
    expect(html).toContain("arXiv");
  });

  it("enlace al NASA Exoplanet Archive", async () => {
    const html = await render();
    expect(html).toContain(
      '<a href="https://exoplanetarchive.ipac.caltech.edu/">NASA Exoplanet Archive</a>',
    );
  });

  it("agradecimiento a arXiv por su interoperabilidad de acceso abierto", async () => {
    const html = await render();
    expect(html).toContain(
      "Gracias a arXiv por el uso de su interoperabilidad de acceso abierto.",
    );
  });

  it("mantiene la frase del pie anterior", async () => {
    const html = await render();
    expect(html).toContain(
      "Nocturna. Resumen semanal del NASA Exoplanet Archive y hallazgos generados automáticamente a partir de arXiv astro-ph.",
    );
  });

  it("el pie es un <footer>", async () => {
    expect(await render()).toMatch(/^<footer/);
  });
});
