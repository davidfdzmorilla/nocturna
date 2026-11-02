import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DigestWriterText } from "./DigestWriterText";

const render = (title: string, summary: string) =>
  renderToStaticMarkup(createElement(DigestWriterText, { text: { title, summary } }));

describe("DigestWriterText", () => {
  it("etiqueta visible y leyenda presentes", () => {
    const html = render("Título", "Uno.");
    expect(html).toContain("Resumen redactado por IA");
    expect(html).toContain(
      "Texto generado automáticamente por IA a partir de los datos de esta semana. Ninguna persona lo ha revisado; los datos de referencia son las tablas de abajo.",
    );
  });

  it("título en h2 con id referenciado por aria-labelledby", () => {
    const html = render("Mi título", "Uno.");
    expect(html).toContain('aria-labelledby="resumen-ia"');
    expect(html).toMatch(/<h2[^>]*id="resumen-ia"[^>]*>Mi título<\/h2>/);
    expect(html).toMatch(/<h2[^>]*text-xl/);
  });

  it("un <p> por párrafo del resumen", () => {
    const html = render("T", "Uno.\n\nDos.\n\nTres.");
    const inSummary = ["Uno.", "Dos.", "Tres."].map((t) => html.includes(`>${t}</p>`));
    expect(inSummary).toEqual([true, true, true]);
    expect(html).toContain("whitespace-pre-line");
  });

  it("HTML en el resumen y el título se escapa", () => {
    const html = render("<b>t</b>", "<script>alert(1)</script> y <b>x</b>");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<b>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&lt;b&gt;x&lt;/b&gt;");
  });

  it("una URL en el texto no se convierte en enlace", () => {
    const html = render("T", "Ver https://example.com/x para más.");
    expect(html).toContain("https://example.com/x");
    expect(html).not.toContain("<a");
  });
});
