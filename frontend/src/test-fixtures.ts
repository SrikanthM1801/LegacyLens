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
      commands: 1, interviews_started: 1, interviews_answered: 4, interviews_approved: 1, documents_generated: 2,
      chats_detected: 1, failures: 1, security_events: 0,
    },
    redactions: { phone: 3, email: 1 },
    consent_funnel: { requested: 3, granted: 2, declined: 0, paused: 0, approval_rate: 1 },
    activity: days.map((date, i) => ({ date, messages_captured: [5, 20, 10, 15][i], questions: [1, 3, 2, 4][i], consent_decisions: 0, failures: 0 })),
    by_category: [{ category: "capture", count: 9, failures: 1 }, { category: "ask", count: 4, failures: 0 }],
    conversations: [
      { scope_id: "c1", name: "Payments Platform", type: "channel", status: "granted", project: "Payments", messages_captured: 30, questions: 5, last_activity: "2026-10-17T10:00:00Z" },
      { scope_id: "c2", name: "Gateway bridge", type: "chat", status: "granted", project: "Payments", messages_captured: 20, questions: 3, last_activity: null },
    ],
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
  { scope_id: "c3", name: "Release planning", type: "chat", status: "pending", approved_by: null, updated_at: "2026-10-12T10:00:00Z", project: "Storefront", account: "Northwind", auto_installed: true },
];
