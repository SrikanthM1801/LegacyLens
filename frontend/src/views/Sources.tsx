import { AreaChart } from "../components/AreaChart";
import { Banner } from "../components/Banner";
import { Card } from "../components/Card";
import { type Column, DataTable } from "../components/DataTable";
import { HBars, type BarItem } from "../components/HBars";
import { Icon } from "../components/Icon";
import { OutcomeChip } from "../components/StatusChip";
import { StatTile } from "../components/StatTile";
import { compact, fmtDateTime, fmtDay, plural } from "../format";
import type { DashboardSummary, FileEventView, RebuildEvent, SyncRun } from "../types";

const CHANGE_LABEL: Record<string, string> = { added: "Added", modified: "Changed", deleted: "Removed" };

const syncCols: Column<SyncRun>[] = [
  { header: "When", render: (r) => fmtDateTime(r.timestamp), muted: true },
  { header: "Kind", render: (r) => (r.full ? "Full" : "Changes only") },
  { header: "Result", render: (r) => <OutcomeChip outcome={r.outcome} /> },
  { header: "Added", num: true, render: (r) => r.added },
  { header: "Changed", num: true, render: (r) => r.modified },
  { header: "Removed", num: true, render: (r) => r.deleted },
  { header: "Failed", num: true, render: (r) => r.failed },
  { header: "Skipped", num: true, render: (r) => r.unsupported },
  { header: "Took", num: true, render: (r) => (r.outcome === "failure" && !r.seconds ? "—" : `${r.seconds.toFixed(1)} s`) },
  { header: "Note", render: (r) => r.error ?? "", muted: true },
];

const fileCols: Column<FileEventView>[] = [
  { header: "When", render: (f) => fmtDateTime(f.timestamp), muted: true },
  { header: "Change", render: (f) => CHANGE_LABEL[f.change] ?? f.change },
  { header: "File", render: (f) => f.file },
  { header: "Folder", render: (f) => f.path || "—", muted: true },
  { header: "Result", render: (f) => <OutcomeChip outcome={f.outcome} /> },
  { header: "Chunks", num: true, render: (f) => f.chunks },
  { header: "Note", render: (f) => f.error ?? (f.status === "unsupported" ? "Type not supported" : f.status === "empty" ? "No text found" : ""), muted: true },
];

const rebuildCols: Column<RebuildEvent>[] = [
  { header: "When", render: (r) => fmtDateTime(r.timestamp), muted: true },
  { header: "Why", render: (r) => r.reason || "—" },
  { header: "SharePoint files", num: true, render: (r) => r.sharepoint_files },
  { header: "Teams messages", num: true, render: (r) => r.teams_messages },
];

/** SharePoint knowledge sources, from the audit trail. Who edited a file is never shown. */
export function Sources({ s }: { s: DashboardSummary }) {
  const src = s.sources;
  if (!src) {
    return <Banner>SharePoint files belong to the shared knowledge base, not to one account or project. Choose <b>All accounts</b> to see them.</Banner>;
  }
  const notLoaded = src.files_failed + src.files_skipped;
  const files = s.activity.map((d) => ({ x: d.date, y: d.files_synced }));
  const filesTable = (
    <DataTable
      rowKey={(d) => d.date} rows={s.activity.filter((d) => d.files_synced > 0)} empty="No files synced in this period."
      columns={[{ header: "Day", render: (d) => fmtDay(d.date) }, { header: "Files", num: true, render: (d) => d.files_synced }]}
    />
  );
  const outcomes: BarItem[] = [
    { key: "added", label: "Added", value: src.files_added },
    { key: "modified", label: "Changed", value: src.files_modified },
    { key: "deleted", label: "Removed", value: src.files_deleted },
    { key: "skipped", label: "Skipped", value: src.files_skipped, detail: "unsupported type or no text" },
    { key: "failed", label: "Failed", value: src.files_failed, detail: src.files_failed ? <><Icon name="alert" size={12} className="ico--critical" />needs a look</> : undefined },
  ];
  const types: BarItem[] = Object.entries(src.by_type).map(([ext, n]) => ({ key: ext, label: `.${ext}`, value: n }));

  return (
    <>
      <Banner>
        Documents come from the project's SharePoint site, checked for changes every few minutes. New and changed files are
        read and indexed; removed files are forgotten. File names are shown here, who edited them is not.
      </Banner>

      <div className="grid grid--tiles" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 170px), 1fr))" }}>
        <StatTile
          label="Sites connected" value={src.sites}
          sub={src.last_sync ? <>Last sync {fmtDateTime(src.last_sync)} {src.last_sync_outcome && <OutcomeChip outcome={src.last_sync_outcome} />}</> : "No sync in this period"}
        />
        <StatTile label="Files added" value={src.files_added} sub={`${compact(src.chunks_indexed)} chunks indexed`} />
        <StatTile label="Files changed" value={src.files_modified} sub="Re-read and re-indexed" />
        <StatTile label="Files removed" value={src.files_deleted} sub="Forgotten by the knowledge base" />
        <StatTile
          label="Not loaded"
          value={<span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
            <Icon name={src.files_failed ? "alert" : "check"} size={22} className={src.files_failed ? "ico--critical" : "ico--good"} />{notLoaded}
          </span>}
          sub={`${src.files_failed} failed · ${src.files_skipped} skipped`}
        />
        <StatTile label="Sync runs" value={src.sync_runs} sub={src.sync_failures ? `${plural(src.sync_failures, "run")} failed` : "None failed"} />
      </div>

      <div className="grid">
        <Card title="Files synced per day" subtitle="Added or changed in SharePoint" table={filesTable}>
          <AreaChart data={files} colorVar="--series-1" name="Files synced" />
        </Card>
        <Card title="What happened to files" subtitle="Each file counted once, even if a rebuild reloaded it">
          <HBars items={outcomes} empty="No file events in this period." />
        </Card>
      </div>

      <div className="grid">
        <Card title="What kinds of files" subtitle="Distinct files added or changed, by type">
          <HBars items={types} empty="No files loaded in this period." />
        </Card>
        <Card title="Knowledge rebuilds" subtitle="When the index was rebuilt from live sources">
          <DataTable rowKey={(r) => r.timestamp} rows={src.rebuilds} columns={rebuildCols} empty="No rebuilds in this period." />
        </Card>
      </div>

      <Card title="Recent syncs" subtitle="Newest first">
        <DataTable rowKey={(r) => r.timestamp + r.outcome} rows={src.recent_syncs} columns={syncCols} empty="No syncs in this period." />
      </Card>
      <Card title="Recent file changes" subtitle="Newest first. Skipped and failed files say why.">
        <DataTable rowKey={(f) => f.timestamp + f.file + f.change} rows={src.recent_files} columns={fileCols} empty="No file changes in this period." />
      </Card>
    </>
  );
}
