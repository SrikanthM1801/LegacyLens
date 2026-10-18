import { Banner } from "../components/Banner";
import { Card } from "../components/Card";
import { type Column, DataTable } from "../components/DataTable";
import { HBars, type BarItem } from "../components/HBars";
import { Icon } from "../components/Icon";
import { ReadinessMeter } from "../components/ReadinessMeter";
import { StatTile } from "../components/StatTile";
import type { DashboardSummary } from "../types";

const KIND_LABEL: Record<string, string> = {
  ask: "Questions", runbook: "Runbook requests", topics: "Topic lists", risks: "Risk lists",
};

/** Onboarding assessments across a scope. Counts by session only: no names, no individual scores. */
export function Onboarding({ s }: { s: DashboardSummary }) {
  const o = s.onboarding;
  const funnel: BarItem[] = [
    { key: "assigned", label: "Assigned", value: o.assigned },
    { key: "learning", label: "Started learning", value: o.started_learning },
    { key: "started", label: "Assessment started", value: o.assessments_started },
    { key: "completed", label: "Completed", value: o.completed },
    { key: "posted", label: "Result shared", value: o.results_posted },
  ];
  const funnelCols: Column<BarItem>[] = [
    { header: "Stage", render: (r) => r.label }, { header: "Sessions", num: true, render: (r) => r.value },
  ];
  const learning: BarItem[] = Object.entries(o.learning_by_kind).sort((a, b) => b[1] - a[1])
    .map(([kind, n]) => ({ key: kind, label: KIND_LABEL[kind] ?? kind, value: n }));
  const none = o.assigned === 0 && o.failed === 0;

  return (
    <>
      <Banner icon="shield" tone="good">
        A newcomer is told up front what is evaluated, sees their own result first, and is scored against cited project
        documents, never style or fluency. This page shows counts only: no names, no individual scores, and the readiness
        spread appears only for a group of at least {o.min_group}.
      </Banner>

      {none ? (
        <Card title="Onboarding assessments"><p className="empty">No onboarding assessments in this period. A team owner starts one with <b>@LegacyLens assign @Name</b>.</p></Card>
      ) : (
        <>
          <div className="grid grid--tiles">
            <StatTile label="Assigned" value={o.assigned} sub="Newcomers given an assessment" />
            <StatTile label="Completed" value={o.completed} sub={o.assigned ? `${o.completed} of ${o.assigned} assigned` : undefined} />
            <StatTile label="Learning questions" value={o.learning_questions} sub="Asked while getting up to speed" />
            <StatTile
              label="Knowledge gaps" value={o.learning_unanswered}
              sub="Questions the knowledge base could not answer: documents worth writing"
            />
            <StatTile
              label="Failures"
              value={<span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <Icon name={o.failed ? "alert" : "check"} size={22} className={o.failed ? "ico--critical" : "ico--good"} />{o.failed}
              </span>}
              sub={o.failed ? "Assignment, evaluation or delivery failed" : "Nothing failed"}
            />
          </div>

          <div className="grid">
            <Card title="Where assessments stand" subtitle="Sessions that reached each stage" table={<DataTable rowKey={(r) => r.key} rows={funnel} columns={funnelCols} />}>
              <HBars items={funnel} empty="No sessions in this period." />
            </Card>
            <Card title="What newcomers ask for" subtitle="Requests made while learning">
              <HBars items={learning} empty="No learning questions in this period." />
            </Card>
          </div>

          <Card
            title="Readiness across the group"
            subtitle={o.readiness && o.average_overall != null ? `Average score ${o.average_overall} out of 100` : undefined}
          >
            {o.readiness
              ? <ReadinessMeter counts={o.readiness} />
              : o.suppressed
                ? <p className="empty">Fewer than {o.min_group} assessments are complete in this scope, so the spread is hidden to protect individuals. Widen the scope or the period to see it.</p>
                : <p className="empty">No assessments have been completed in this period.</p>}
          </Card>
        </>
      )}
    </>
  );
}
