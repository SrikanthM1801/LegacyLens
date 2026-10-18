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
  runbooks_requested: number;
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
  files_synced: number;
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

export interface SyncRun {
  timestamp: string;
  outcome: Outcome;
  added: number;
  modified: number;
  deleted: number;
  unchanged: number;
  failed: number;
  unsupported: number;
  chunks_indexed: number;
  seconds: number;
  full: boolean;
  error: string | null;
}

export interface FileEventView {
  timestamp: string;
  change: "added" | "modified" | "deleted" | string;
  file: string;
  path: string;
  status: string;
  chunks: number;
  outcome: Outcome;
  error: string | null;
}

export interface RebuildEvent {
  timestamp: string;
  reason: string;
  sharepoint_files: number;
  teams_messages: number;
}

/** Knowledge loaded from SharePoint. Org level only: the site belongs to the shared knowledge base. */
export interface SourcesSummary {
  sites: number;
  files_added: number;
  files_modified: number;
  files_deleted: number;
  files_failed: number;
  files_skipped: number;
  chunks_indexed: number;
  sync_runs: number;
  sync_failures: number;
  last_sync: string | null;
  last_sync_outcome: Outcome | null;
  by_type: Record<string, number>;
  recent_syncs: SyncRun[];
  recent_files: FileEventView[];
  rebuilds: RebuildEvent[];
}

export type Readiness = "ready" | "almost" | "needs_more_time";

/** Counts by session. No names, no individual scores; the spread is hidden for groups under `min_group`. */
export interface OnboardingSummary {
  assigned: number;
  started_learning: number;
  assessments_started: number;
  completed: number;
  results_posted: number;
  failed: number;
  learning_questions: number;
  learning_unanswered: number;
  learning_by_kind: Record<string, number>;
  readiness: Record<Readiness, number> | null;
  average_overall: number | null;
  min_group: number;
  suppressed: boolean;
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
  sources: SourcesSummary | null;
  onboarding: OnboardingSummary;
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
  type: "channel" | "chat" | "site" | "mailbox" | string;
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

// ---------------------------------------------------------------------------
// Knowledge detail (GET /risks, /interviews, /artifacts, /departures)
// ---------------------------------------------------------------------------

export type RiskLevel = "high" | "medium" | "low";

export interface Citation {
  artifact_id: string;
  snippet: string;
  url_or_path: string | null;
  title: string;
  artifact_type: string | null;
  author: string | null;
  timestamp: string | null;
  score: number | null;
}

/** One topic's knowledge concentration. Owners are deliberately not shown: topics, not people. */
export interface RiskItem {
  id: string;
  topic: string;
  entity_id: string | null;
  entity_type: "system" | "process" | "decision" | string | null;
  summary: string;
  owner_candidates: string[];
  bus_factor: number;
  coverage_score: number;
  activity_score: number;
  risk_score: number;
  risk_level: RiskLevel;
  missing_doc_types: string[];
  evidence: Citation[];
  computed_at: string;
}

export interface RiskListResponse {
  items: RiskItem[];
  total: number;
  computed_at: string;
}

export interface InterviewQuestion {
  id: string;
  text: string;
  rationale: string;
  evidence: Citation[];
  answered: boolean;
  follow_up_of: string | null;
}

export interface InterviewAnswer {
  id: string;
  question_id: string;
  text: string;
  answered_at: string;
}

export type InterviewStatus = "pending" | "in_progress" | "completed";

export interface InterviewSession {
  id: string;
  risk_id: string;
  topic: string;
  status: InterviewStatus;
  questions: InterviewQuestion[];
  answers: InterviewAnswer[];
  created_at: string;
  completed_at: string | null;
}

export interface GeneratedDoc {
  id: string;
  type: "runbook" | "handover" | "decision_log" | string;
  title: string;
  topic: string | null;
  markdown: string;
  citations: Citation[];
  generated_at: string;
  model: string;
  open_questions: string[];
}

export interface AtRiskTopic {
  risk_id: string;
  topic: string;
  share: number;
  risk_score: number;
  risk_level: RiskLevel;
  coverage: number;
  risk_after: number | null;
  level_after: RiskLevel | null;
}

export type DepartureStatus = "alerted" | "interviewing" | "reviewing" | "publishing" | "completed" | "no_risk" | "cancelled";

export interface Departure {
  id: string;
  person_name: string;
  source: "out_of_office" | "self" | "owner";
  starts_at: string | null;
  ends_at: string | null;
  detected_at: string;
  status: DepartureStatus;
  at_risk: AtRiskTopic[];
  session_ids: string[];
  handover_doc_id: string | null;
  handover_url: string | null;
  error: string | null;
}
