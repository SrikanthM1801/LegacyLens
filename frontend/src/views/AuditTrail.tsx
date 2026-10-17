import { useState } from "react";
import { api } from "../api";
import { Banner } from "../components/Banner";
import { Card } from "../components/Card";
import { type Column, DataTable } from "../components/DataTable";
import { OutcomeChip } from "../components/StatusChip";
import { fmtDateTime, titleCase } from "../format";
import { useAsync } from "../hooks";
import type { DashboardEvent, Outcome, Scope } from "../types";

const HIDE = new Set(["version", "live_m365"]);
const isEmpty = (v: unknown) =>
  v == null || v === "" || (Array.isArray(v) && v.length === 0) || (typeof v === "object" && Object.keys(v as object).length === 0);

/** "chunks: 6 · reply: answer": the first few safe details, objects flattened. */
function summarize(details: Record<string, unknown>): string {
  return Object.entries(details)
    .filter(([k, v]) => !HIDE.has(k) && !isEmpty(v))
    .slice(0, 3)
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

const cols: Column<DashboardEvent>[] = [
  { header: "When", render: (e) => fmtDateTime(e.timestamp), muted: true },
  { header: "Event", render: (e) => e.action },
  { header: "Result", render: (e) => <OutcomeChip outcome={e.outcome} /> },
  { header: "By", render: (e) => e.actor_role, muted: true },
  { header: "Where", render: (e) => e.scope_name ?? "—" },
  { header: "Count", num: true, render: (e) => e.count ?? "" },
  { header: "Details", render: (e) => summarize(e.details), muted: true },
];

export function AuditTrail({ scope, days, adminKey }: { scope: Scope; days: number; adminKey: string }) {
  const [category, setCategory] = useState("");
  const [outcome, setOutcome] = useState<Outcome | "">("");
  const [limit, setLimit] = useState(100);
  const { data, loading, error } = useAsync(
    (signal) => api.events(scope, days, adminKey, { category, outcome, limit }, signal),
    [scope.level, scope.id, days, adminKey, category, outcome, limit],
  );
  const categories = [...new Set((data?.actions ?? []).map((a) => a.split(".")[0]))].sort();

  return (
    <>
      <Banner>
        The audit trail records what LegacyLens did: ids, counts and results. It never holds message text, file
        contents or questions, and people appear as a role (member, LegacyLens, system), not a name.
      </Banner>
      <Card title="Audit trail" subtitle={data ? `Showing ${data.items.length} of ${data.total} events, newest first` : "Loading…"}>
        <div className="filters" style={{ borderBottom: 0, paddingTop: 0 }}>
          <div className="field">
            <label htmlFor="a-cat">Kind</label>
            <select id="a-cat" className="select" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All kinds</option>
              {categories.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
              {category && !categories.includes(category) && <option value={category}>{titleCase(category)}</option>}
            </select>
          </div>
          <div className="field">
            <label htmlFor="a-out">Result</label>
            <select id="a-out" className="select" value={outcome} onChange={(e) => setOutcome(e.target.value as Outcome | "")}>
              <option value="">Any result</option>
              <option value="success">OK</option><option value="failure">Failed</option><option value="skipped">Skipped</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="a-limit">Show</label>
            <select id="a-limit" className="select" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
              {[50, 100, 200, 500].map((n) => <option key={n} value={n}>{n} events</option>)}
            </select>
          </div>
        </div>
        <div className={loading && data ? "dim" : undefined}>
          {error
            ? <p className="error" role="alert">{error.message}</p>
            : <DataTable rowKey={(e) => e.id} rows={data?.items ?? []} columns={cols} empty="No events match these filters." />}
        </div>
      </Card>
    </>
  );
}
