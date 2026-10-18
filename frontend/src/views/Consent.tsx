import { useState } from "react";
import { api } from "../api";
import { Banner } from "../components/Banner";
import { Card } from "../components/Card";
import { type Column, DataTable } from "../components/DataTable";
import { Icon } from "../components/Icon";
import { ConsentChip, CONSENT_META } from "../components/StatusChip";
import { fmtDateTime } from "../format";
import { useAsync } from "../hooks";
import type { ConsentRow, ConsentStatus, DashboardSummary, GovernanceItem, Scope } from "../types";

const TYPE_LABEL: Record<string, string> = { channel: "channel", chat: "group chat", site: "SharePoint site", mailbox: "mailbox" };

const cols: Column<ConsentRow>[] = [
  { header: "Conversation or source", render: (r) => <>{r.name} <span style={{ color: "var(--muted)" }}>· {TYPE_LABEL[r.type] ?? r.type}{r.auto_installed ? " · joined on detection" : ""}</span></> },
  { header: "Project", render: (r) => r.project },
  { header: "Account", render: (r) => r.account, muted: true },
  { header: "Status", render: (r) => <ConsentChip status={r.status} /> },
  { header: "Decided by", render: (r) => r.approved_by ?? "—", muted: true },
  { header: "Updated", render: (r) => fmtDateTime(r.updated_at), muted: true },
];

const govCols: Column<GovernanceItem>[] = [
  { header: "What is kept", render: (g) => g.what },
  { header: "Where", render: (g) => g.where },
  {
    header: "Can hold project content",
    render: (g) => (
      <span className="chip">
        <Icon name={g.contains_content ? "info" : "check"} className={g.contains_content ? "ico--info" : "ico--good"} />
        {g.contains_content ? "Yes, redacted" : "No"}
      </span>
    ),
  },
  { header: "How long and when", render: (g) => g.retention, muted: true },
];

export function Consent({ summary, scope, adminKey }: { summary: DashboardSummary; scope: Scope; adminKey: string }) {
  const [filter, setFilter] = useState<ConsentStatus | "">("");
  const { data, loading, error } = useAsync((signal) => api.consents(scope, adminKey, signal), [scope.level, scope.id, adminKey]);
  const rows = (data ?? []).filter((r) => !filter || r.status === filter);
  const k = summary.kpis;

  return (
    <>
      <Banner icon="shield" tone="good">
        LegacyLens reads a conversation only after someone clicks <b>Allow</b> in it. {k.conversations_total - k.conversations_granted} of
        {" "}{k.conversations_total} here are not allowed, so nothing from them is read. 1:1 chats are never touched, and anyone can type
        {" "}<b>@LegacyLens pause</b> to stop.
      </Banner>

      <Card title="What is processed and where it is kept" subtitle="Everything LegacyLens stores, in one list">
        <DataTable rowKey={(g) => g.what} rows={summary.governance} columns={govCols} />
      </Card>

      <Card
        title="Consent register"
        subtitle="Every conversation and source LegacyLens knows about, and who decided. This is the one place a person is named: accountability for the decision. A SharePoint site is allowed by an admin configuring it."
      >
        <div className="field" style={{ marginBottom: 12 }}>
          <label htmlFor="c-status">Show</label>
          <select id="c-status" className="select" value={filter} onChange={(e) => setFilter(e.target.value as ConsentStatus | "")}>
            <option value="">All statuses</option>
            {(Object.keys(CONSENT_META) as ConsentStatus[]).map((s) => <option key={s} value={s}>{CONSENT_META[s].label}</option>)}
          </select>
        </div>
        <div className={loading && data ? "dim" : undefined}>
          {error
            ? <p className="error" role="alert">{error.message}</p>
            : <DataTable rowKey={(r) => r.scope_id} rows={rows} columns={cols} empty="No conversations match." />}
        </div>
      </Card>
    </>
  );
}
