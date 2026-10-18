import type { ConsentRow, DashboardSummary, EventsResponse, HierarchyNode } from "./types";

export const TREE: HierarchyNode = {
  level: "org", id: "org", name: "Org", conversations: 3,
  children: [
    {
      level: "account", id: "acc_a", name: "Contoso", conversations: 2,
      children: [
        { level: "project", id: "prj_1", name: "Payments", conversations: 2, children: [] },
      ],
    },
    {
      level: "account", id: "acc_b", name: "Northwind", conversations: 1,
      children: [{ level: "project", id: "prj_2", name: "Storefront", conversations: 1, children: [] }],
    },
  ],
};

export function makeSummary(over: Partial<DashboardSummary> = {}): DashboardSummary {
  const days = ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17"];
  return {
    data_source: "demo",
    scope: TREE,
    since: "2026-09-17T00:00:00Z", until: "2026-10-17T12:00:00Z", generated_at: "2026-10-17T12:00:00Z",
    kpis: {
      conversations_total: 3, conversations_granted: 2, conversations_pending: 1, conversations_declined: 0, conversations_paused: 0,
      messages_read: 60, messages_captured: 50, duplicates_skipped: 10, chunks_indexed: 48,
      questions_answered: 8, questions_unanswered: 2, questions_failed: 0, answer_rate: 0.8, avg_confidence: 0.7, avg_citations: 2.5,
      commands: 1, runbooks_requested: 0, interviews_started: 1, interviews_answered: 4, interviews_approved: 1, documents_generated: 2,
      chats_detected: 1, failures: 1, security_events: 0,
    },
    redactions: { phone: 3, email: 1 },
    consent_funnel: { requested: 3, granted: 2, declined: 0, paused: 0, approval_rate: 1 },
    activity: days.map((date, i) => ({ date, files_synced: [0, 2, 1, 3][i], messages_captured: [5, 20, 10, 15][i], questions: [1, 3, 2, 4][i], consent_decisions: 0, failures: 0 })),
    by_category: [{ category: "capture", count: 9, failures: 1 }, { category: "ask", count: 4, failures: 0 }],
    conversations: [
      { scope_id: "c1", name: "Payments Platform", type: "channel", status: "granted", project: "Payments", messages_captured: 30, questions: 5, last_activity: "2026-10-17T10:00:00Z" },
      { scope_id: "c2", name: "Gateway bridge", type: "chat", status: "granted", project: "Payments", messages_captured: 20, questions: 3, last_activity: null },
    ],
    sources: {
      sites: 1, files_added: 10, files_modified: 6, files_deleted: 1, files_failed: 1, files_skipped: 1, chunks_indexed: 95,
      sync_runs: 20, sync_failures: 2, last_sync: "2026-10-17T10:00:00Z", last_sync_outcome: "success",
      by_type: { docx: 6, pdf: 4, xlsx: 3, pptx: 3 },
      recent_syncs: [
        { timestamp: "2026-10-17T10:00:00Z", outcome: "success", added: 0, modified: 2, deleted: 0, unchanged: 8, failed: 0, unsupported: 0, chunks_indexed: 8, seconds: 1.4, full: false, error: null },
        { timestamp: "2026-10-16T10:00:00Z", outcome: "failure", added: 0, modified: 0, deleted: 0, unchanged: 8, failed: 1, unsupported: 0, chunks_indexed: 0, seconds: 0, full: false, error: "403 Forbidden" },
      ],
      recent_files: [
        { timestamp: "2026-10-17T10:00:00Z", change: "modified", file: "Release process.docx", path: "Engineering/Process", status: "loaded", chunks: 7, outcome: "success", error: null },
        { timestamp: "2026-10-16T10:00:00Z", change: "added", file: "Corrupt export.pdf", path: "Operations", status: "failed", chunks: 0, outcome: "failure", error: "could not read the PDF" },
        { timestamp: "2026-10-15T10:00:00Z", change: "added", file: "Team photo.png", path: "People", status: "unsupported", chunks: 0, outcome: "skipped", error: null },
      ],
      rebuilds: [{ timestamp: "2026-09-27T07:00:00Z", reason: "first start without sample data", sharepoint_files: 10, teams_messages: 0 }],
    },
    onboarding: {
      assigned: 5, started_learning: 5, assessments_started: 4, completed: 4, results_posted: 4, failed: 1,
      learning_questions: 15, learning_unanswered: 3, learning_by_kind: { ask: 9, runbook: 2, topics: 2, risks: 1 },
      readiness: { ready: 1, almost: 2, needs_more_time: 1 }, average_overall: 64, min_group: 3, suppressed: false,
    },
    risk_impact: [{ topic: "Payment Gateway", approved_at: "2026-10-13T10:00:00Z", risk_before: 1, risk_after: 0.1 }],
    recent_failures: [{
      id: "e1", timestamp: "2026-10-16T10:00:00Z", action: "capture.backfill", outcome: "failure", source: "graph",
      actor_role: "LegacyLens", scope_type: "chat", scope_name: "Gateway bridge", count: null, details: { error: "HTTP 429" },
    }],
    governance: [{ what: "Audit trail", where: "Blob", contains_content: false, retention: "Append-only" }],
    notes: ["Demo data: these events are generated, not read from Teams."],
    ...over,
  };
}

export const EVENTS: EventsResponse = {
  total: 2, actions: ["capture.message", "consent.granted"],
  items: [
    { id: "x1", timestamp: "2026-10-17T10:00:00Z", action: "capture.message", outcome: "success", source: "teams", actor_role: "LegacyLens", scope_type: "chat", scope_name: "Gateway bridge", count: 3, details: { chunks: 3, redactions: {} } },
    { id: "x2", timestamp: "2026-10-17T09:00:00Z", action: "consent.granted", outcome: "success", source: "teams", actor_role: "member", scope_type: "chat", scope_name: "Gateway bridge", count: null, details: {} },
  ],
};

export const CONSENTS: ConsentRow[] = [
  { scope_id: "c1", name: "Payments Platform", type: "channel", status: "granted", approved_by: "Priya Nair", updated_at: "2026-10-10T10:00:00Z", project: "Payments", account: "Contoso", auto_installed: false },
  { scope_id: "site1", name: "Payments Platform SharePoint", type: "site", status: "granted", approved_by: "admin configuration (SHAREPOINT_SITE_URL)", updated_at: "2026-10-09T10:00:00Z", project: "Shared knowledge source", account: "", auto_installed: false },
  { scope_id: "c3", name: "Release planning", type: "chat", status: "pending", approved_by: null, updated_at: "2026-10-12T10:00:00Z", project: "Storefront", account: "Northwind", auto_installed: true },
];
