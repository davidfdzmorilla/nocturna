import type { IndependentConfirmation } from "@/lib/api/types";
import { arxivAbsUrl, safeArchiveUrl } from "@/lib/externalLinks";
import {
  PARAMETER_LABELS,
  formatSigma,
  formatWithErrors,
} from "@/lib/measurementFormat";

type Props = {
  data: IndependentConfirmation;
};

export function ConfirmationData({ data }: Props) {
  const archiveUrl = safeArchiveUrl(data.archive_url);
  const refArxiv = arxivAbsUrl(data.reference.arxiv_id);
  const caption = `${PARAMETER_LABELS[data.parameter]} de ${data.paper_planet_name}: medida del artículo frente a la referencia del archivo`;

  return (
    <>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <caption className="pb-2 text-left text-text-muted">{caption}</caption>
          <thead>
            <tr>
              <th scope="col" className="border-b border-border p-2">
                Medida del artículo
              </th>
              <th scope="col" className="border-b border-border p-2">
                Diferencia con la referencia
              </th>
            </tr>
          </thead>
          <tbody>
            {data.measurements.map((m, index) => (
              <tr key={index}>
                <th
                  scope="row"
                  className="border-b border-border p-2 font-normal"
                >
                  <span className="block">{data.paper_planet_name}</span>
                  <span className="block">{formatWithErrors(m)}</span>
                </th>
                <td className="border-b border-border p-2">
                  {data.sigmas[index] !== undefined
                    ? `${formatSigma(data.sigmas[index])} σ`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-sm text-text">
        Referencia del archivo: {data.reference.refname},{" "}
        {formatWithErrors(data.reference)}.
        {` Publicada en el archivo el ${data.reference.releasedate}.`}{" "}
        Diferencia máxima: {formatSigma(data.max_sigma)} σ.
      </p>
      {refArxiv ? (
        <p className="mt-1 text-sm">
          <a href={refArxiv} rel="noopener noreferrer">
            Ver la referencia en arXiv
          </a>
        </p>
      ) : null}
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
