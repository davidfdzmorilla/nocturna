import { useId } from "react";
import type {
  DigestEntry,
  DigestReference,
  ParameterChange,
  TransitionKind,
} from "@/lib/api/types";
import { SECTION_LABELS, groupByReference } from "@/lib/archiveDigest";
import { formatDay } from "@/lib/dates";
import { safeArchiveUrl } from "@/lib/externalLinks";
import {
  PARAMETER_LABELS,
  formatArchiveValue,
  formatArchiveValueRounded,
} from "@/lib/measurementFormat";
import {
  CAPTION,
  TABLE,
  TH,
  TH_ROW,
  TableScroll,
} from "./TableScroll";

type Props = {
  kind: TransitionKind;
  entries: DigestEntry[];
};

const EMPTY_TEXT = "Ninguno esta semana.";
const NO_PARAMETER_CHANGES = "Sin cambios en masa, radio ni periodo.";
const NO_PROVENANCE = "–";

function PlanetName({ entry }: { entry: DigestEntry }) {
  const url = safeArchiveUrl(entry.planet_url);
  return url ? (
    <a href={url} rel="noopener noreferrer">
      {entry.pl_name}
    </a>
  ) : (
    <>{entry.pl_name}</>
  );
}

function ReferenceFields({
  label,
  reference,
}: {
  label: string;
  reference: DigestReference;
}) {
  return (
    <>
      <dt className="text-text-muted">{label}</dt>
      <dd className="break-words">{reference.reference}</dd>
      <dt className="text-text-muted">Publicación</dt>
      <dd className="break-words">{reference.pl_pubdate ?? "–"}</dd>
      <dt className="text-text-muted">Alta en el archivo</dt>
      <dd className="break-words">{formatDay(reference.releasedate)}</dd>
    </>
  );
}

function ValueCell({
  change,
  side,
}: {
  change: ParameterChange;
  side: "old" | "new";
}) {
  const value = side === "old" ? change.old : change.new;
  const provenance =
    side === "old" ? change.old_mass_provenance : change.new_mass_provenance;
  return (
    <td className="border-b border-border px-3 py-2" title={formatArchiveValue(value)}>
      <span className="block whitespace-nowrap">
        {formatArchiveValueRounded(value)}
      </span>
      {change.parameter === "mass" ? (
        <span className="block text-text-muted">
          Procedencia de la masa: {provenance ?? NO_PROVENANCE}
        </span>
      ) : null}
    </td>
  );
}

function ChangesTable({ entry }: { entry: DigestEntry }) {
  const captionId = useId();
  return (
    <div className="mt-4">
      <TableScroll labelledBy={captionId}>
        <table className={`${TABLE} min-w-[30rem]`}>
          <caption id={captionId} className={CAPTION}>
            Parámetros de {entry.pl_name} que difieren entre la referencia
            anterior y la nueva (valores redondeados)
          </caption>
          <thead>
            <tr>
              <th scope="col" className={TH}>
                Parámetro
              </th>
              <th scope="col" className={TH}>
                Referencia anterior
              </th>
              <th scope="col" className={TH}>
                Referencia nueva
              </th>
            </tr>
          </thead>
          <tbody>
            {entry.parameter_changes.map((change) => (
              <tr key={change.parameter} className="align-top">
                <th scope="row" className={TH_ROW}>
                  {PARAMETER_LABELS[change.parameter]}
                </th>
                <ValueCell change={change} side="old" />
                <ValueCell change={change} side="new" />
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-text-muted">
          Valores exactos del archivo
        </summary>
        <ul className="mt-2 space-y-2 tabular-nums">
          {entry.parameter_changes.map((change) => (
            <li key={change.parameter}>
              <span className="text-text-muted">
                {PARAMETER_LABELS[change.parameter]}
              </span>
              <span className="block break-words">
                Referencia anterior: {formatArchiveValue(change.old)}
              </span>
              <span className="block break-words">
                Referencia nueva: {formatArchiveValue(change.new)}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function EntryCard({ entry }: { entry: DigestEntry }) {
  const showBoth = entry.kind === "changed";
  const single = entry.new ?? entry.old;
  return (
    <article className="rounded-lg border border-border bg-surface p-4 sm:p-5">
      <h3 className="text-lg font-semibold leading-snug break-words">
        <PlanetName entry={entry} />
      </h3>
      <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-0.5 text-sm sm:grid-cols-[auto_minmax(0,1fr)] [&>dt]:mt-2 sm:[&>dt]:mt-0">
        {showBoth ? (
          <>
            {entry.old ? (
              <ReferenceFields label="Referencia anterior" reference={entry.old} />
            ) : null}
            {entry.new ? (
              <ReferenceFields label="Referencia nueva" reference={entry.new} />
            ) : null}
          </>
        ) : single ? (
          <ReferenceFields label="Referencia" reference={single} />
        ) : null}
        <dt className="text-text-muted">Detectado</dt>
        <dd className="break-words">{formatDay(entry.detected_at)}</dd>
      </dl>
      {entry.parameter_changes.length > 0 ? (
        <ChangesTable entry={entry} />
      ) : showBoth ? (
        <p className="mt-3 text-sm text-text-muted">{NO_PARAMETER_CHANGES}</p>
      ) : null}
    </article>
  );
}

function NewPlanetGroups({ entries }: { entries: DigestEntry[] }) {
  return (
    <div className="flex flex-col gap-4">
      {groupByReference(entries).map((group) => (
        <div
          key={group.reference}
          className="rounded-lg border border-border bg-surface p-4 sm:p-5"
        >
          <h3 className="text-lg font-semibold leading-snug break-words">
            {group.reference} · {group.entries.length}{" "}
            {group.entries.length === 1 ? "planeta" : "planetas"}
          </h3>
          <ul className="mt-3 flex flex-wrap gap-2 text-sm">
            {group.entries.map((entry) => (
              <li
                key={entry.pl_name}
                className="max-w-full rounded-full border border-border bg-surface-raised px-3 py-1 [overflow-wrap:anywhere]"
              >
                <PlanetName entry={entry} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function DigestSection({ kind, entries }: Props) {
  return (
    <section className="mt-8">
      <h2 className="font-display text-xl break-words text-text sm:text-2xl">
        {SECTION_LABELS[kind]} ({entries.length})
      </h2>
      {entries.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">{EMPTY_TEXT}</p>
      ) : kind === "new_planet" ? (
        <div className="mt-3">
          <NewPlanetGroups entries={entries} />
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-4">
          {entries.map((entry) => (
            <EntryCard key={`${entry.kind}:${entry.pl_name}:${entry.detected_at}`} entry={entry} />
          ))}
        </div>
      )}
    </section>
  );
}
