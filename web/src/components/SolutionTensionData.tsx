import type {
  SolutionTension,
  SolutionTensionSolution,
} from "@/lib/api/types";
import { candidateLegend } from "@/lib/findingTypes";
import { arxivAbsUrl, safeArchiveUrl } from "@/lib/externalLinks";
import {
  PARAMETER_LABELS,
  parameterTitle,
  formatNumber,
  formatSigma,
  formatWithErrors,
} from "@/lib/measurementFormat";
import {
  NO_OTHERS_TEXT,
  SIGMA_NOTE,
  SOLUTION_TENSION_LABELS,
  formatReleaseDate,
  knownLabels,
  referenceRoleText,
} from "@/lib/solutionTension";

type Props = {
  data: SolutionTension;
};

const TH = "border-b border-border p-2";
const TD = "border-b border-border p-2";

function SolutionCell({ solution }: { solution: SolutionTensionSolution }) {
  const arxiv = arxivAbsUrl(solution.arxiv_id);
  return (
    <>
      <span className="block">{solution.reference}</span>
      <span className="block">{formatWithErrors(solution)}</span>
      {solution.is_default ? (
        <span className="block text-text-muted">
          Solución por defecto del archivo
        </span>
      ) : null}
      {arxiv ? (
        <a
          href={arxiv}
          rel="noopener noreferrer"
          aria-label={`Ver ${solution.reference} en arXiv`}
        >
          Ver en arXiv
        </a>
      ) : null}
    </>
  );
}

export function SolutionTensionData({ data }: Props) {
  const archiveUrl = safeArchiveUrl(data.archive_url);
  const parameter = PARAMETER_LABELS[data.parameter];
  const labels = knownLabels(data.labels);
  const release = formatReleaseDate(data.new_releasedate);

  return (
    <>
      <p className="mt-2 text-sm text-text">{candidateLegend("solution_tension")}</p>

      {labels.length > 0 ? (
        <ul className="mt-3 space-y-2 text-sm">
          {labels.map((label) => (
            <li key={label}>
              <span className="mr-2 inline-block rounded border border-notice-border bg-notice-bg px-2 py-0.5 text-xs font-medium text-notice-text">
                {SOLUTION_TENSION_LABELS[label].chip}
              </span>
              {SOLUTION_TENSION_LABELS[label].sentence}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <caption className="pb-2 text-left text-text-muted">
            {`${parameterTitle(data.parameter)} de ${data.planet_name}: dos soluciones del NASA Exoplanet Archive`}
          </caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>
                Solución
              </th>
              <th scope="col" className={TH}>
                Trabajo y valor
              </th>
            </tr>
          </thead>
          <tbody>
            <tr className="align-top">
              <th scope="row" className={`${TD} font-normal`}>
                Solución nueva
              </th>
              <td className={TD}>
                <SolutionCell solution={data.new} />
                {release ? (
                  <span className="block text-text-muted">{release}</span>
                ) : null}
              </td>
            </tr>
            <tr className="align-top">
              <th scope="row" className={`${TD} font-normal`}>
                Referencia
              </th>
              <td className={TD}>
                <SolutionCell solution={data.reference} />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p className="mt-2 text-sm text-text-muted">
        {referenceRoleText(data.reference_role)}
      </p>
      <p className="mt-2 text-sm text-text">
        Diferencia: {formatSigma(data.sigma)} σ (umbral:{" "}
        {formatNumber(data.threshold_sigma)} σ).
      </p>
      <p className="mt-1 text-sm text-text-muted">{SIGMA_NOTE}</p>

      <h3 className="mt-6 text-base font-semibold text-text">
        Otras soluciones del archivo
      </h3>
      {data.others.length === 0 ? (
        <p className="mt-2 text-sm text-text-muted">{NO_OTHERS_TEXT}</p>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <caption className="sr-only">
              {`Otras soluciones de ${parameter} de ${data.planet_name} en el archivo`}
            </caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>
                  Solución
                </th>
                <th scope="col" className={TH}>
                  Diferencia con la nueva
                </th>
              </tr>
            </thead>
            <tbody>
              {data.others.map((o, index) => (
                <tr key={index} className="align-top">
                  <th scope="row" className={`${TD} font-normal`}>
                    <SolutionCell solution={o.solution} />
                  </th>
                  <td className={TD}>{formatSigma(o.sigma)} σ</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {archiveUrl ? (
        <p className="mt-3 text-sm">
          <a href={archiveUrl} rel="noopener noreferrer">
            Ficha del planeta en el NASA Exoplanet Archive
          </a>
        </p>
      ) : null}
    </>
  );
}
