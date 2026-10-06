import type { CatalogTension } from "@/lib/api/types";
import { CANDIDATE_LEGEND } from "@/lib/findingTypes";
import { arxivAbsUrl, safeArchiveUrl } from "@/lib/externalLinks";
import {
  PARAMETER_LABELS,
  formatNumber,
  formatSigma,
  formatWithErrors,
} from "@/lib/measurementFormat";
import { EvidenceQuote } from "./EvidenceQuote";

type Props = {
  data: CatalogTension;
  sourceUrl: string | null;
};

export function CatalogTensionData({ data, sourceUrl }: Props) {
  const archiveUrl = safeArchiveUrl(data.archive_url);
  const caption = `${PARAMETER_LABELS[data.parameter]} de ${data.planet_name}: medida del artículo frente a soluciones previas del archivo`;

  return (
    <>
      <p className="mt-2 text-sm text-text">{CANDIDATE_LEGEND}</p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <caption className="pb-2 text-left text-text-muted">{caption}</caption>
          <thead>
            <tr>
              <th scope="col" className="border-b border-border p-2">
                Medida del artículo
              </th>
              <th scope="col" className="border-b border-border p-2">
                Cita del resumen
              </th>
              <th scope="col" className="border-b border-border p-2">
                Solución previa del archivo
              </th>
              <th scope="col" className="border-b border-border p-2">
                Diferencia
              </th>
            </tr>
          </thead>
          <tbody>
            {data.comparisons.map((c, index) => {
              const priorArxiv = arxivAbsUrl(c.prior.arxiv_id);
              return (
                <tr key={index} className="align-top">
                  <th
                    scope="row"
                    className="border-b border-border p-2 font-normal"
                  >
                    <span className="block">{c.paper.planet_name}</span>
                    <span className="block">{formatWithErrors(c.paper)}</span>
                  </th>
                  <td className="border-b border-border p-2">
                    <EvidenceQuote
                      evidence={c.paper.evidence}
                      sourceUrl={sourceUrl}
                    />
                  </td>
                  <td className="border-b border-border p-2">
                    <span className="block">{c.prior.reference}</span>
                    <span className="block">{formatWithErrors(c.prior)}</span>
                    {c.prior.is_default ? (
                      <span className="block text-text-muted">
                        Solución por defecto del archivo
                      </span>
                    ) : null}
                    {priorArxiv ? (
                      <a href={priorArxiv} rel="noopener noreferrer">
                        Ver la previa en arXiv
                      </a>
                    ) : null}
                  </td>
                  <td className="border-b border-border p-2">
                    {formatSigma(c.sigma)} σ
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-sm text-text-muted">
        Umbral: {formatNumber(data.threshold_sigma)} σ. σ de referencia:{" "}
        {formatSigma(data.reference_sigma)}.
      </p>
      {archiveUrl ? (
        <p className="mt-1 text-sm">
          <a href={archiveUrl} rel="noopener noreferrer">
            Ficha del planeta en el NASA Exoplanet Archive
          </a>
        </p>
      ) : null}
    </>
  );
}
