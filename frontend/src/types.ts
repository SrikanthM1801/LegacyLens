// Mirrors backend/app/models.py (section 11, Dashboard). That file stays the source of truth.

export type Level = "org" | "account" | "project";

export interface HierarchyNode {
  level: Level;
  id: string;
  name: string;
  conversations: number;
  children: HierarchyNode[];
}

export interface Kpis {
  conversations_total: number;
  conversations_granted: number;
  conversations_pending: number;
  conversations_declined: number;
  conversations_paused: number;
  messages_read: number;
  messages_captured: number;
  duplicates_skipped: number;
  chunks_indexed: number;
  questions_answered: number;
  questions_unanswered: number;
  questions_failed: number;
  answer_rate: number | null;
  avg_confidence: number | null;
  avg_citations: number | null;
  commands: number;
  interviews_started: number;
  interviews_answered: number;
  interviews_approved: number;
  documents_generated: number;
  chats_detected: number;
  failures: number;
  security_events: number;
}

export interface DailyActivity {
  date: string;
  messages_captured: number;
  questions: number;
  consent_decisions: number;
  failures: number;
}

export interface CategoryCount {
  category: string;
  count: number;
  failures: number;
}

export interface ConversationActivity {
  scope_id: string;
  name: string;
  type: string;
  status: ConsentStatus;
  project: string;
  messages_captured: number;
  questions: number;
  last_activity: string | null;
}

export interface RiskImpact {
  topic: string;
  approved_at: string;
  risk_before: number;
  risk_after: number;
}

export type Outcome = "success" | "failure" | "skipped";

export interface DashboardEvent {
  id: string;
  timestamp: string;
  action: string;
  outcome: Outcome;
  source: string;
  actor_role: "member" | "LegacyLens" | "system";
  scope_type: string | null;
  scope_name: string | null;
  count: number | null;
  details: Record<string, unknown>;
}

export interface GovernanceItem {
  what: string;
  where: string;
  contains_content: boolean;
  retention: string;
}

export interface ConsentFunnel {
  requested: number;
  granted: number;
  declined: number;
  paused: number;
  approval_rate: number | null;
}

export type DataSource = "azure" | "memory" | "demo";

export interface DashboardSummary {
  /** Where the numbers come from. Never mixed. */
  data_source: DataSource;
  scope: HierarchyNode;
  since: string;
  until: string;
  generated_at: string;
  kpis: Kpis;
  redactions: Record<string, number>;
  consent_funnel: ConsentFunnel;
  activity: DailyActivity[];
  by_category: CategoryCount[];
  conversations: ConversationActivity[];
  risk_impact: RiskImpact[];
  recent_failures: DashboardEvent[];
  governance: GovernanceItem[];
  notes: string[];
}

export interface EventsResponse {
  items: DashboardEvent[];
  total: number;
  actions: string[];
}

export type ConsentStatus = "granted" | "pending" | "declined" | "paused";

export interface ConsentRow {
  scope_id: string;
  name: string;
  type: string;
  status: ConsentStatus;
  approved_by: string | null;
  updated_at: string;
  project: string;
  account: string;
  auto_installed: boolean;
}

export interface Scope {
  level: Level;
  id: string;
}
