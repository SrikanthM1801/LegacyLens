import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Banner } from "../components/Banner";
import { Card, Empty } from "../components/Card";
import { type Column, DataTable } from "../components/DataTable";
import { HBars, type BarItem } from "../components/HBars";
import { Icon } from "../components/Icon";
import { CitationList, InterviewChip, LEVEL_META, LevelChip, ScoreBar } from "../components/Knowledge";
import { StatTile } from "../components/StatTile";
import { fmtDateTime, fmtDay, pct, plural, titleCase } from "../format";
import { useAsync } from "../hooks";
import type { GeneratedDoc, InterviewSession, RiskItem, RiskLevel } from "../types";

const sameTopic = (a: string | null | undefined, b: string) => !!a && a.trim().toLowerCase() === b.trim().toLowerCase();
const latest = (xs: InterviewSession[]) => [...xs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];

/** Loads risks, interviews and generated documents together; the risk and document tabs share it. */
export function useKnowledge(adminKey: string) {
  return useAsync(
    (signal) => Promise.all([api.risks(adminKey, signal), api.interviews(adminKey, signal), api.documents(adminKey, signal)])
      .then(([risks, interviews, docs]) => ({ risks, interviews, docs })),
    [adminKey],
  );
}

/**
 * Knowledge risk per topic, with the evidence behind each score. Topics, never people: who holds the
 * knowledge is not shown here, only how many people do.
 */
export function Risks({ adminKey, onOpenDoc }: { adminKey: string; onOpenDoc: (id: string) => void }) {
  const { data, loading, error } = useKnowledge(adminKey);
  const [level, setLevel] = useState<RiskLevel | "">("");
  const [openId, setOpenId] = useState<string | null>(() => new URLSearchParams(window.location.search).get("risk"));
  const detailRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (openId) detailRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }); }, [openId]);

  if (error) return <Banner icon="alert" tone="critical">{error.message}</Banner>;
  if (!data) return <p className="empty" role="status">Loading…</p>;

  const risks = data.risks.items;
  const byLevel = (l: RiskLevel) => risks.filter((r) => r.risk_level === l).length;
  const single = risks.filter((r) => r.bus_factor <= 1).length;
  const avgCoverage = risks.length ? risks.reduce((a, r) => a + r.coverage_score, 0) / risks.length : null;
  const approved = data.interviews.filter((s) => s.status === "completed").length;
  const open = data.interviews.length - approved;
  const interviewFor = (r: RiskItem) => latest(data.interviews.filter((s) => s.risk_id === r.id || sameTopic(s.topic, r.topic)));
  const docsFor = (r: RiskItem) => data.docs.filter((d) => sameTopic(d.topic, r.topic));

  const top: BarItem[] = risks.slice(0, 8).map((r) => ({
    key: r.id, label: r.topic, value: r.risk_score,
    detail: <>{plural(r.bus_factor, "person", "people")} · {pct(r.coverage_score)} documented</>,
  }));
  const rows = risks.filter((r) => !level || r.risk_level === level);
  const selected = risks.find((r) => r.id === openId) ?? null;

  const cols: Column<RiskItem>[] = [
    {
      header: "Topic",
      render: (r) => (
        <button className="linkbtn" aria-expanded={r.id === openId} onClick={() => setOpenId(r.id === openId ? null : r.id)}>
          {r.topic}
        </button>
      ),
    },
    { header: "Kind", render: (r) => (r.entity_type ? titleCase(r.entity_type) : "—"), muted: true },
    { header: "Risk", render: (r) => <LevelChip level={r.risk_level} /> },
    { header: "Score", num: true, render: (r) => r.risk_score.toFixed(2) },
    { header: "People who know it", num: true, render: (r) => r.bus_factor },
    { header: "Documented", num: true, render: (r) => pct(r.coverage_score) },
    { header: "Interview", render: (r) => { const s = interviewFor(r); return s ? <InterviewChip status={s.status} /> : <span className="muted">None yet</span>; } },
    { header: "Documents", num: true, render: (r) => docsFor(r).length },
  ];

  return (
    <div className={loading ? "dim" : undefined} style={{ display: "grid", gap: 16 }}>
      <Banner icon="shield" tone="good">
        A risk score describes how concentrated a topic's knowledge is, not how anyone performs. It is high when a topic is
        busy, few people know it and little is written down. It falls once an interview is approved. Computed {fmtDateTime(data.risks.computed_at)}.
      </Banner>

      <div className="grid grid--tiles">
        <StatTile label="Topics at high risk" value={byLevel("high")} sub={`${byLevel("medium")} medium · ${byLevel("low")} low · ${risks.length} tracked`} />
        <StatTile label="Known by one person" value={single} sub={single ? "Topics with no second person who knows them" : "Every topic has at least two people"} />
        <StatTile label="Documented on average" value={pct(avgCoverage)} sub="Covered by documents or approved interviews" />
        <StatTile label="Interviews approved" value={approved} sub={open ? `${open} still open` : "None open"} />
        <StatTile label="Documents generated" value={data.docs.length} sub={`${data.docs.filter((d) => d.type === "runbook").length} runbooks · ${data.docs.filter((d) => d.type === "handover").length} handover packs`} />
      </div>

      <Card
        title="Most at-risk topics" subtitle="Risk score, 0 to 1"
        table={<DataTable rowKey={(r) => r.id} rows={risks.slice(0, 8)} columns={cols.slice(0, 6)} />}
      >
        <HBars items={top} colorVar="--series-2" format={(v) => v.toFixed(2)} empty="No knowledge risks yet. Ingest some project history first." />
      </Card>

      <Card title="All topics" subtitle="Select a topic to see why it scored as it did, its interview and its documents">
        <div className="pills" role="radiogroup" aria-label="Risk level" style={{ marginBottom: 12 }}>
          {(["", "high", "medium", "low"] as const).map((l) => (
            <button key={l || "all"} className="pill" role="radio" aria-checked={level === l} onClick={() => setLevel(l)}>
              {level === l && <Icon name="check" size={13} />}{l ? LEVEL_META[l].label : "All"} ({l ? byLevel(l) : risks.length})
            </button>
          ))}
        </div>
        <DataTable rowKey={(r) => r.id} rows={rows} columns={cols} empty="No topics at this level." />
      </Card>

      <div ref={detailRef}>
        {selected && (
          <RiskDetail risk={selected} sessions={data.interviews.filter((s) => s.risk_id === selected.id || sameTopic(s.topic, selected.topic))}
            docs={docsFor(selected)} onOpenDoc={onOpenDoc} onClose={() => setOpenId(null)} />
        )}
      </div>
    </div>
  );
}

function RiskDetail({ risk, sessions, docs, onOpenDoc, onClose }: {
  risk: RiskItem; sessions: InterviewSession[]; docs: GeneratedDoc[]; onOpenDoc: (id: string) => void; onClose: () => void;
}) {
  const s = latest(sessions);
  return (
    <section className="card detail" aria-label={`Details for ${risk.topic}`}>
      <div className="card__head">
        <div>
          <h2 className="card__title">{risk.topic} <LevelChip level={risk.risk_level} /></h2>
          {/* risk.summary is not shown: it names the leading person, and this page is about topics, not people. */}
          <p className="card__sub">
            {risk.entity_type ? titleCase(risk.entity_type) : "Topic"} · {plural(risk.bus_factor, "person knows", "people know")} it ·
            {" "}{pct(risk.coverage_score)} documented · {plural(risk.evidence.length, "source")} of evidence
          </p>
        </div>
        <button className="btn btn--ghost btn--small" onClick={onClose}>Close</button>
      </div>

      <div className="grid grid--tiles">
        <ScoreBar label="Risk" value={risk.risk_score} hint="activity × 1/people × (1 − documented)" />
        <ScoreBar label="Activity" value={risk.activity_score} hint="How much the project works on it lately" />
        <ScoreBar label="Documented" value={risk.coverage_score} invert hint="Documents and approved interviews" />
        <div className="score">
          <div className="score__head"><span>People who know it</span><b>{risk.bus_factor}</b></div>
          <div className="score__hint">{risk.bus_factor <= 1 ? "One person holds most of it: an interview is the fastest fix." : "Shared across people, so less fragile."}</div>
        </div>
      </div>

      {risk.missing_doc_types.length > 0 && (
        <p style={{ marginTop: 16 }}>
          <span className="muted">Missing documents: </span>
          {risk.missing_doc_types.map((t) => <span key={t} className="chip" style={{ marginRight: 6 }}><Icon name="info" className="ico--warning" />{titleCase(t)}</span>)}
        </p>
      )}

      <h3 className="detail__h">Evidence behind this score</h3>
      <CitationList items={risk.evidence} empty="No evidence recorded for this topic." />

      <h3 className="detail__h">Interview</h3>
      {s ? <InterviewSummary session={s} /> : (
        <Empty>No interview yet. In Teams, a team owner types <b>@LegacyLens handover @Name</b>, or the person going on leave types <b>@LegacyLens away until</b> <i>date</i>.</Empty>
      )}

      <h3 className="detail__h">Documents about this topic</h3>
      {docs.length ? (
        <ul className="plain">
          {docs.map((d) => (
            <li key={d.id}>
              <button className="linkbtn" onClick={() => onOpenDoc(d.id)}>{d.title}</button>
              <span className="muted"> · {titleCase(d.type)} · {plural(d.citations.length, "source")} · {fmtDay(d.generated_at)}</span>
            </li>
          ))}
        </ul>
      ) : <Empty>No runbook yet. In Teams: <b>@LegacyLens runbook {risk.topic}</b>.</Empty>}
    </section>
  );
}

/** Questions with their evidence. Answers are shown only once approved: drafts stay with the interviewee. */
export function InterviewSummary({ session }: { session: InterviewSession }) {
  const answered = session.questions.filter((q) => q.answered).length;
  const approved = session.status === "completed";
  const answerOf = (qid: string) => session.answers.find((a) => a.question_id === qid);
  return (
    <div>
      <p style={{ marginBottom: 8 }}>
        <InterviewChip status={session.status} />{" "}
        <span className="muted">
          {answered} of {plural(session.questions.length, "question")} answered · started {fmtDay(session.created_at)}
          {session.completed_at ? ` · approved ${fmtDay(session.completed_at)}` : ""}
        </span>
      </p>
      <ol className="qa">
        {session.questions.map((q) => (
          <li key={q.id}>
            <div><b>{q.follow_up_of ? "Follow-up: " : ""}{q.text}</b></div>
            {q.rationale && <div className="muted">{q.rationale}</div>}
            {q.evidence[0] && <blockquote>“{q.evidence[0].snippet}” <span className="muted">· {q.evidence[0].title}</span></blockquote>}
            {approved && answerOf(q.id)
              ? <div className="qa__answer">{answerOf(q.id)!.text}</div>
              : <div className="muted">{q.answered ? "Answered: draft, shown here once approved" : "Not answered yet"}</div>}
          </li>
        ))}
      </ol>
    </div>
  );
}
