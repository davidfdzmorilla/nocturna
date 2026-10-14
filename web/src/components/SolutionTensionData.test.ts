import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { SolutionTension } from "@/lib/api/types";
import {
  HD_114082_B_PERIOD,
  HD_202206_C_MASS,
  SYNTHETIC_WITH_LABELS,
  detailWith,
} from "@/lib/findingFixtures";
import { FindingData } from "./FindingData";

const html = (s: SolutionTension) =>
  renderToStaticMarkup(createElement(FindingData, { finding: detailWith(s) }));

const count = (text: string, sub: string) => text.split(sub).length - 1;

describe("SolutionTensionData", () => {
  it("masa de HD 202206 c", () => {
    const out = html(HD_202206_C_MASS);
    expect(out).toContain("982,09 ± 44,5 M⊕");
    expect(out).toContain("5689 +922 / −572 M⊕");
    expect(out).toContain("Diferencia: 8,20 σ (umbral: 3 σ).");
    expect(out).toContain("Referencia: la solución más reciente de otro trabajo");
    expect(out).not.toContain("Referencia: la solución por defecto del archivo.");
    expect(out).toContain("Entró en el archivo el 20 de agosto de 2026.");
    expect(out).toContain("Ninguna otra solución publicada y confirmada");
    expect(out).not.toContain("Depende del modelo");
    expect(out).not.toContain("Planeta controvertido");
  });

  it("'Solución por defecto del archivo' solo en la nueva", () => {
    const out = html(HD_202206_C_MASS);
    expect(count(out, "Solución por defecto del archivo")).toBe(1);
    const newPos = out.indexOf("Cao et al. 2026");
    const defPos = out.indexOf("Solución por defecto del archivo");
    const refPos = out.indexOf("Benedict &amp; Harrison 2017");
    expect(newPos).toBeLessThan(defPos);
    expect(defPos).toBeLessThan(refPos);
  });

  it("caption, scope, enlace a la ficha y leyenda D9", () => {
    const out = html(HD_202206_C_MASS);
    expect(out).toContain(
      "<caption",
    );
    expect(out).toContain(
      "Masa de HD 202206 c: dos soluciones del NASA Exoplanet Archive",
    );
    expect(out).toContain('scope="col"');
    expect(out).toContain('scope="row"');
    expect(out).toContain(`href="${HD_202206_C_MASS.archive_url}"`);
    expect(out).toContain("Ficha del planeta en el NASA Exoplanet Archive");
    expect(out).toContain(
      "Candidato: discrepancia entre soluciones del NASA Exoplanet Archive calculada automáticamente; ninguna persona la ha verificado.",
    );
    expect(out).toContain("Otras soluciones del archivo");
  });

  it("periodo de HD 114082 b con la nota de σ", () => {
    const out = html(HD_114082_B_PERIOD);
    expect(out).toContain("225,5504 ± 0,0004 d");
    expect(out).toContain("109,75 +0,4 / −0,37 d");
    expect(out).toContain("289,50 σ");
    expect(out).toContain(
      "La σ mide la diferencia en unidades de los errores publicados; con errores muy pequeños puede ser enorme aunque el cambio de valor sea lo relevante.",
    );
    expect(out).toContain("Entró en el archivo el 3 de septiembre de 2026.");
  });

  it("la nota de σ aparece también en la sintética", () => {
    expect(html(SYNTHETIC_WITH_LABELS)).toContain("La σ mide la diferencia");
  });

  it("sintética: dos etiquetas (nombre y frase), others en orden con σ, rol default", () => {
    const out = html(SYNTHETIC_WITH_LABELS);
    expect(out).toContain("Depende del modelo");
    expect(out).toContain("la diferencia puede depender de la solución elegida.");
    expect(out).toContain("Planeta controvertido");
    expect(out).toContain("marca este planeta como controvertido.");
    expect(out).toContain("Referencia: la solución por defecto del archivo.");
    expect(out).not.toContain("Entró en el archivo");
    expect(out).toContain("Diferencia con la nueva");
    const a = out.indexOf("Otra et al. 2023");
    const b = out.indexOf("Tercera et al. 2021");
    expect(a).toBeGreaterThan(-1);
    expect(a).toBeLessThan(b);
    expect(out).toContain("2,10 σ");
    expect(out).toContain("3,90 σ");
    expect(out.indexOf("2,10 σ")).toBeLessThan(out.indexOf("3,90 σ"));
    expect(out).not.toContain("Ninguna otra solución");
  });

  it("referencias de terceros escapadas", () => {
    const out = html({
      ...HD_202206_C_MASS,
      new: { ...HD_202206_C_MASS.new, reference: "A <i>x</i> et al." },
      reference: { ...HD_202206_C_MASS.reference, reference: "<i>y</i>" },
    });
    expect(out).not.toContain("<i>");
    expect(out).toContain("A &lt;i&gt;x&lt;/i&gt; et al.");
  });

  it("arxiv_id válido enlaza; javascript: o inválido no", () => {
    const ok = html(SYNTHETIC_WITH_LABELS);
    expect(ok).toContain('href="https://arxiv.org/abs/2610.01234"');
    expect(ok).toContain('href="https://arxiv.org/abs/2401.05678"');
    expect(count(ok, "Ver en arXiv")).toBe(2);
    expect(ok).not.toContain("javascript:");

    for (const bad of ["javascript:alert(1)", "../x", "2610.1", ""]) {
      const out = html({
        ...HD_202206_C_MASS,
        new: { ...HD_202206_C_MASS.new, arxiv_id: bad },
      });
      expect(out).not.toContain("Ver en arXiv");
      expect(out).not.toContain("javascript:");
    }
  });

  it("ficha con javascript: no enlaza", () => {
    const out = html({ ...HD_202206_C_MASS, archive_url: "javascript:alert(1)" });
    expect(out).not.toContain("javascript:");
    expect(out).not.toContain("Ficha del planeta");
  });

  it("tabla de otras: primera celda th scope=row y aria-label del enlace", () => {
    const out = html(SYNTHETIC_WITH_LABELS);
    const others = out.slice(out.indexOf("Diferencia con la nueva"));
    expect(others).toContain('<th scope="row" class="border-b border-border p-2 font-normal">');
    expect(others).not.toContain("<td class=\"border-b border-border p-2\"><span");
    expect(out).toContain('aria-label="Ver Nueva et al. 2026 en arXiv"');
    expect(out).toContain('aria-label="Ver Defecto et al. 2024 en arXiv"');
    expect(out).toContain(">Ver en arXiv<");
  });

  it("descarta etiquetas desconocidas y extreme_sigma", () => {
    const out = html({
      ...HD_202206_C_MASS,
      labels: ["otra" as never, "extreme_sigma" as never],
    });
    expect(out).not.toContain("<ul");
    expect(out).not.toContain("extreme");
  });
});
