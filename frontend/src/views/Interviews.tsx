import { useState } from "react";
import { api } from "../api";
import { Banner } from "../components/Banner";
import { Card } from "../components/Card";
import { type Column, DataTable } from "../components/DataTable";
import { DepartureChip, InterviewChip, LevelChip } from "../components/Knowledge";
import { fmtDay, plural } from "../format";
import { useAsync } from "../hooks";
import type { Departure, InterviewSession } from "../types";
import { InterviewSummary } from "./Risks";

const WHY: Record<Departure["source"], string> = {
  out_of_office: "Out-of-office reply", self: "Told LegacyLens (away)", owner: "Team owner started a handover",
};

const when = (d: Departure) =>
  d.starts_at || d.ends_at
    ? `${d.starts_at ? fmtDay(d.starts_at) : "now"} → ${d.ends_at ? fmtDay(d.ends_at) : "open-ended"}`
    : "—";

const depCols: Column<Departure>[] = [
  { header: "Person", render: (d) => d.person_name },
  { header: "Why", render: (d) => WHY[d.source] ?? d.source, muted: true },
  { header: "Away", render: when, muted: true },
  { header: "Status", render: (d) => <DepartureChip status={d.status} /> },
  {
    header: "Topics at risk, before → after",
    render: (d) => d.at_risk.length === 0 ? <span className="muted">None</span> : (
      <ul className="plain">
        {d.at_risk.map((t) => (
          <li key={t.risk_id}>
            {t.topic} <LevelChip level={t.risk_level} /> {t.risk_score.toFixed(2)}
            {t.risk_after != null && <> → <b>{t.risk_after.toFixed(2)}</b> {t.level_after && <LevelChip level={t.level_after} />}</>}
            <span className="muted"> · holds {Math.round(t.share * 100)}%</span>
          </li>
        ))}
      </ul>
    ),
  },
  {
    header: "Handover pack",
    render: (d) => d.handover_url
      ? <a href={d.handover_url} target="_blank" rel="noreferrer">Open</a>
      : <span className="muted">{d.error ?? "Not yet"}</span>,
  },
];

/** Handovers (people going on leave or leaving) and every interview, with its questions and evidence. */
export function Interviews({ adminKey }: { adminKey: string }) {
  const deps = useAsync((signal) => api.departures(adminKey, signal), [adminKey]);
  const ivs = useAsync((signal) => api.interviews(adminKey, signal), [adminKey]);
  const [openId, setOpenId] = useState<string | null>(null);

  const sessions = [...(ivs.data ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const cols: Column<InterviewSession>[] = [
    {
      header: "Topic",
      render: (s) => <button className="linkbtn" aria-expanded={s.id === openId} onClick={() => setOpenId(s.id === openId ? null : s.id)}>{s.topic}</button>,
    },
    { header: "Status", render: (s) => <InterviewChip status={s.status} /> },
    { header: "Answered", num: true, render: (s) => `${s.questions.filter((q) => q.answered).length} / ${s.questions.length}` },
    { header: "Follow-ups", num: true, render: (s) => s.questions.filter((q) => q.follow_up_of).length },
    { header: "Started", render: (s) => fmtDay(s.created_at), muted: true },
    { header: "Approved", render: (s) => (s.completed_at ? fmtDay(s.completed_at) : "—"), muted: true },
  ];
  const open = sessions.find((s) => s.id === openId);

  return (
    <>
      <Banner>
        A handover starts when someone sets an out-of-office reply, types <b>@LegacyLens away until</b> <i>date</i>, or a team
        owner types <b>@LegacyLens handover @Name</b>. LegacyLens interviews them 1:1 about the topics only they know, and
        publishes nothing until they approve.
      </Banner>

      <Card title="Handovers" subtitle={deps.data ? plural(deps.data.length, "handover") + ", newest first" : "Loading…"}>
        {deps.error
          ? <p className="error" role="alert">{deps.error.message}</p>
          : <DataTable rowKey={(d) => d.id} rows={deps.data ?? []} columns={depCols} empty="No handovers yet." />}
      </Card>

      <Card title="Interviews" subtitle="Select a topic to see the questions and the evidence each one quotes">
        {ivs.error
          ? <p className="error" role="alert">{ivs.error.message}</p>
          : <DataTable rowKey={(s) => s.id} rows={sessions} columns={cols} empty="No interviews yet." />}
      </Card>

      {open && (
        <section className="card detail" aria-label={`Interview about ${open.topic}`}>
          <div className="card__head">
            <h2 className="card__title">Interview: {open.topic}</h2>
            <button className="btn btn--ghost btn--small" onClick={() => setOpenId(null)}>Close</button>
          </div>
          <InterviewSummary session={open} />
        </section>
      )}
    </>
  );
}
