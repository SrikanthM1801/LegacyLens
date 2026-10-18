import { useEffect, useRef } from "react";
import { Banner } from "../components/Banner";
import { Card, Empty } from "../components/Card";
import { type Column, DataTable } from "../components/DataTable";
import { CitationList, Markdown } from "../components/Knowledge";
import { fmtDateTime, titleCase } from "../format";
import type { GeneratedDoc } from "../types";
import { useKnowledge } from "./Risks";

/** Generated runbooks and handover packs, readable here with every [n] linked to its source. */
export function Documents({ adminKey, openId, onOpen }: { adminKey: string; openId: string | null; onOpen: (id: string | null) => void }) {
  const { data, error } = useKnowledge(adminKey);
  const viewRef = useRef<HTMLElement>(null);
  useEffect(() => { if (openId) viewRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }); }, [openId, data]);

  if (error) return <Banner icon="alert" tone="critical">{error.message}</Banner>;
  if (!data) return <p className="empty" role="status">Loading…</p>;

  const docs = [...data.docs].sort((a, b) => b.generated_at.localeCompare(a.generated_at));
  const doc = docs.find((d) => d.id === openId);
  const cols: Column<GeneratedDoc>[] = [
    { header: "Title", render: (d) => <button className="linkbtn" aria-expanded={d.id === openId} onClick={() => onOpen(d.id === openId ? null : d.id)}>{d.title}</button> },
    { header: "Kind", render: (d) => titleCase(d.type) },
    { header: "Topic", render: (d) => d.topic ?? "—", muted: true },
    { header: "Sources", num: true, render: (d) => d.citations.length },
    { header: "Open questions", num: true, render: (d) => d.open_questions.length },
    { header: "Generated", render: (d) => fmtDateTime(d.generated_at), muted: true },
  ];

  return (
    <>
      <Banner>
        Every statement in a generated document carries a numbered source. Open questions are what the sources could not
        answer: the next interview should cover them.
      </Banner>
      <Card title="Generated documents" subtitle="Newest first">
        <DataTable rowKey={(d) => d.id} rows={docs} columns={cols}
          empty="No documents yet. In Teams, type @LegacyLens runbook followed by a topic." />
      </Card>

      {doc && (
        <section className="card detail" ref={viewRef} aria-label={doc.title}>
          <div className="card__head">
            <div>
              <h2 className="card__title">{doc.title}</h2>
              <p className="card__sub">{titleCase(doc.type)} · generated {fmtDateTime(doc.generated_at)} · model {doc.model}</p>
            </div>
            <button className="btn btn--ghost btn--small" onClick={() => onOpen(null)}>Close</button>
          </div>
          <Markdown text={doc.markdown.split(/\n#{1,6}\s*Sources\b/)[0]} citePrefix={`cite-${doc.id}`} />
          {doc.open_questions.length > 0 && (
            <>
              <h3 className="detail__h">Open questions</h3>
              <ul className="plain">{doc.open_questions.map((q) => <li key={q}>{q}</li>)}</ul>
            </>
          )}
          <h3 className="detail__h">Sources</h3>
          {doc.citations.length ? <CitationList items={doc.citations} idPrefix={`cite-${doc.id}`} /> : <Empty>No sources.</Empty>}
        </section>
      )}
    </>
  );
}
