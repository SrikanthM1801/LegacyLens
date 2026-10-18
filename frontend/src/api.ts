import type {
  ConsentRow, DashboardSummary, Departure, EventsResponse, GeneratedDoc, HierarchyNode, InterviewSession, Outcome,
  RiskListResponse, Scope,
} from "./types";

// Dev server: the API is on :8000. Production build: the backend serves this page, so call the same origin
// (relative URLs). VITE_API_BASE_URL overrides either, e.g. a UI hosted apart from the API.
const BASE: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? (import.meta.env.PROD ? "" : "http://localhost:8000");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
  /** The server wants an admin key (none sent, or a wrong one). */
  get needsKey(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

type Params = Record<string, string | number | undefined>;

async function get<T>(path: string, params: Params, adminKey: string, signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") qs.set(k, String(v));
  const url = `${BASE}${path}${qs.size ? `?${qs}` : ""}`;
  let res: Response;
  try {
    res = await fetch(url, { headers: adminKey ? { "X-Admin-Key": adminKey } : {}, signal });
  } catch {
    throw new ApiError(0, `Cannot reach the LegacyLens API at ${BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

/** since/until for "the last N days", computed when the request is made (not on every render). */
export function window(days: number, now: Date = new Date()): { since: string; until: string } {
  return { since: new Date(now.getTime() - days * 86_400_000).toISOString(), until: now.toISOString() };
}

export const api = {
  hierarchy: (key: string, signal?: AbortSignal) => get<HierarchyNode>("/dashboard/hierarchy", {}, key, signal),
  summary: (scope: Scope, days: number, key: string, signal?: AbortSignal) =>
    get<DashboardSummary>("/dashboard/summary", { level: scope.level, id: scope.id, ...window(days) }, key, signal),
  events: (
    scope: Scope, days: number, key: string,
    f: { category?: string; outcome?: Outcome | ""; limit?: number } = {}, signal?: AbortSignal,
  ) =>
    get<EventsResponse>(
      "/dashboard/events",
      { level: scope.level, id: scope.id, ...window(days), category: f.category, outcome: f.outcome || undefined, limit: f.limit ?? 100 },
      key, signal,
    ),
  consents: (scope: Scope, key: string, signal?: AbortSignal) =>
    get<ConsentRow[]>("/dashboard/consents", { level: scope.level, id: scope.id }, key, signal),
  // Knowledge detail: the live knowledge base, not the audit trail, so not filtered by scope or period.
  risks: (key: string, signal?: AbortSignal) => get<RiskListResponse>("/risks", {}, key, signal),
  interviews: (key: string, signal?: AbortSignal) => get<InterviewSession[]>("/interviews", {}, key, signal),
  documents: (key: string, signal?: AbortSignal) => get<GeneratedDoc[]>("/artifacts", {}, key, signal),
  departures: (key: string, signal?: AbortSignal) => get<Departure[]>("/departures", {}, key, signal),
};
