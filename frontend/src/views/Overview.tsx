import { Banner } from "../components/Banner";
import { Card, Empty } from "../components/Card";
import { ConsentMeter } from "../components/ConsentMeter";
import { type Column, DataTable } from "../components/DataTable";
import { Dumbbell } from "../components/Dumbbell";
import { AreaChart } from "../components/AreaChart";
import { HBars, type BarItem } from "../components/HBars";
import { Icon } from "../components/Icon";
import { ConsentChip } from "../components/StatusChip";
import { StatTile } from "../components/StatTile";
import { compact, fmtDateTime, fmtDay, pct, plural, titleCase } from "../format";
import type { DashboardEvent, DashboardSummary } from "../types";

const sum = (o: Record<string, number>) => Object.values(o).reduce((a, b) => a + b, 0);

const failureCols: Column<DashboardEvent>[] = [
  { header: "When", render: (e) => fmtDateTime(e.timestamp), muted: true },
  { header: "What", render: (e) => e.action },
  { header: "Where", render: (e) => e.scope_name ?? "—" },
  { header: "Why", render: (e) => String(e.details.error ?? e.details.reason ?? "—"), muted: true },
];

export function Overview({ s }: { s: DashboardSummary }) {
  const k = s.kpis;
  const isOrg = s.scope.level === "org";
  const noRead = k.conversations_total - k.conversations_granted;
  const redacted = sum(s.redactions);

  const messages = s.activity.map((d) => ({ x: d.date, y: d.messages_captured }));
  const questions = s.activity.map((d) => ({ x: d.date, y: d.questions }));
  const dayTable = (key: "messages_captured" | "questions", label: string) => (
    <DataTable
      rowKey={(d) => d.date} rows={s.activity.filter((d) => d[key] > 0)} empty="No activity in this period."
      columns={[{ header: "Day", render: (d) => fmtDay(d.date) }, { header: label, num: true, render: (d) => d[key] }]}
    />
  );

  const conversations: BarItem[] = s.conversations.map((c) => ({
    key: c.scope_id, label: c.name, value: c.messages_captured,
    detail: <>{plural(c.questions, "question")}</>,
    tip: (
      <>
        <div className="tip__title">{c.project} · {c.type}</div>
        <div className="tip__value">{c.name}</div>
        <div className="tip__row"><span className="tip__value">{compact(c.messages_captured)}</span><span className="tip__name">messages learned</span></div>
        <div className="tip__row"><span className="tip__value">{c.questions}</span><span className="tip__name">questions asked</span></div>
        <div style={{ marginTop: 4 }}><ConsentChip status={c.status} /></div>
      </>
    ),
  }));
  const categories: BarItem[] = s.by_category.map((c) => ({
    key: c.category, label: titleCase(c.category), value: c.count,
    detail: c.failures > 0 ? <><Icon name="alert" size={12} className="ico--critical" />{c.failures} failed</> : undefined,
  }));
  const redactionItems: BarItem[] = Object.entries(s.redactions).sort((a, b) => b[1] - a[1])
    .map(([kind, n]) => ({ key: kind, label: titleCase(kind), value: n }));
  const f = s.consent_funnel;
  const funnel: BarItem[] = [
    { key: "requested", label: "Asked for consent", value: f.requested },
    { key: "granted", label: "Allowed", value: f.granted },
    { key: "declined", label: "Not now", value: f.declined },
    { key: "paused", label: "Paused later", value: f.paused },
  ];

  return (
    <>
      {s.notes.map((n) => <Banner key={n}>{n}</Banner>)}

      <Card title="What LegacyLens may read" subtitle="Nothing is read from a conversation until someone clicks Allow in it.">
        <div className="hero">
          <StatTile
            hero label="Conversations allowed" value={k.conversations_granted} small={`of ${k.conversations_total}`}
            sub={noRead > 0
              ? `The other ${noRead} ${noRead === 1 ? "is" : "are"} paused, declined or still waiting, so ${noRead === 1 ? "it is" : "they are"} never read.`
              : "Every conversation here has been allowed."}
          />
          <ConsentMeter counts={{
            granted: k.conversations_granted, paused: k.conversations_paused,
            pending: k.conversations_pending, declined: k.conversations_declined,
          }} />
        </div>
      </Card>

      <div className="grid grid--tiles">
        <StatTile
          label="Messages learned" value={compact(k.messages_captured)}
          sub={k.duplicates_skipped > 0 ? `${compact(k.duplicates_skipped)} already known, skipped` : `${compact(k.chunks_indexed)} chunks indexed`}
        />
        <StatTile
          label="Questions answered" value={k.questions_answered}
          sub={k.answer_rate == null ? "No questions in this period" : `${pct(k.answer_rate)} answered with sources`}
        />
        <StatTile
          label="Redacted before storing" value={compact(redacted)}
          sub={redacted ? Object.entries(s.redactions).map(([kind, n]) => `${titleCase(kind).toLowerCase()} ${n}`).join(" · ") : "Nothing needed redacting"}
        />
        <StatTile
          label="Failures"
          value={<span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <Icon name={k.failures ? "alert" : "check"} size={22} className={k.failures ? "ico--critical" : "ico--good"} />{k.failures}
          </span>}
          sub={k.failures ? `${plural(k.security_events, "security event")}` : "Nothing failed"}
        />
        {isOrg && s.sources && (
          <StatTile
            label="Files synced from SharePoint" value={s.sources.files_added + s.sources.files_modified}
            sub={`${s.sources.files_deleted} removed · ${s.sources.files_failed} failed`}
          />
        )}
        <StatTile
          label="Onboarding assessments completed" value={s.onboarding.completed}
          sub={s.onboarding.assigned ? `${s.onboarding.assigned} assigned` : "None assigned in this period"}
        />
        {isOrg
          ? <StatTile label="Interviews approved" value={k.interviews_approved} sub={`${plural(k.documents_generated, "document")} generated`} />
          : <StatTile label="Chats detected" value={k.chats_detected} sub="Project group chats LegacyLens joined to ask for consent" />}
      </div>

      <div className="grid">
        <Card title="Messages learned per day" subtitle="Only from allowed conversations" table={dayTable("messages_captured", "Messages")}>
          <AreaChart data={messages} colorVar="--series-1" name="Messages learned" />
        </Card>
        <Card title="Questions per day" subtitle="Asked in Teams or through the API" table={dayTable("questions", "Questions")}>
          <AreaChart data={questions} colorVar="--series-2" name="Questions" />
        </Card>
      </div>

      <div className="grid">
        <Card title="Where LegacyLens is learning" subtitle="Messages learned per conversation">
          <HBars items={conversations} />
        </Card>
        <Card title="What LegacyLens did" subtitle="Events by kind in this period">
          <HBars items={categories} />
        </Card>
      </div>

      <div className="grid">
        <Card title="Redacted before storing" subtitle="Phone numbers, card numbers and outside emails never reach the knowledge base">
          <HBars items={redactionItems} empty="Nothing needed redacting in this period." />
        </Card>
        <Card
          title="Consent decisions"
          subtitle={f.approval_rate == null ? "No Allow or Not now decisions in this period" : `${pct(f.approval_rate)} of decisions were Allow`}
        >
          <HBars items={funnel} />
        </Card>
      </div>

      {isOrg && (
        <div className="grid" style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
          <Card
            title="Knowledge risk before and after interviews"
            subtitle="A lower score means the knowledge is no longer in one person's head"
            table={
              <DataTable
                rowKey={(r) => r.topic + r.approved_at} rows={s.risk_impact} empty="No interviews approved in this period."
                columns={[
                  { header: "Topic", render: (r) => r.topic },
                  { header: "Before", num: true, render: (r) => r.risk_before.toFixed(2) },
                  { header: "After", num: true, render: (r) => r.risk_after.toFixed(2) },
                  { header: "Approved", render: (r) => fmtDay(r.approved_at), muted: true },
                ]}
              />
            }
          >
            <Dumbbell items={s.risk_impact} />
          </Card>
        </div>
      )}

      <Card title="Recent failures" subtitle="Newest first. Errors are status codes, never message content.">
        {s.recent_failures.length === 0
          ? <Empty>Nothing failed in this period.</Empty>
          : <DataTable rowKey={(e) => e.id} rows={s.recent_failures} columns={failureCols} />}
      </Card>
    </>
  );
}
