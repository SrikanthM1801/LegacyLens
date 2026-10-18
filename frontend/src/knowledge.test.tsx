import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { Markdown } from "./components/Knowledge";
import { TREE, makeSummary } from "./test-fixtures";
import type { Citation, Departure, GeneratedDoc, InterviewSession, RiskItem } from "./types";

const cite = (over: Partial<Citation> = {}): Citation => ({
  artifact_id: "art_1", snippet: "Gateway timed out again, restarted the pod", url_or_path: "https://sp.example/doc.docx",
  title: "INC-204 gateway outage", artifact_type: "ticket", author: null, timestamp: "2026-10-01T10:00:00Z", score: null, ...over,
});
const risk = (over: Partial<RiskItem>): RiskItem => ({
  id: "risk_1", topic: "Payment gateway", entity_id: "sys_1", entity_type: "system", summary: "led by Ravi, 90%",
  owner_candidates: ["person_ravi"], bus_factor: 1, coverage_score: 0.1, activity_score: 0.9, risk_score: 0.81, risk_level: "high",
  missing_doc_types: ["runbook"], evidence: [cite()], computed_at: "2026-10-17T12:00:00Z", ...over,
});
const RISKS = [risk({}), risk({ id: "risk_2", topic: "Release process", bus_factor: 3, coverage_score: 0.8, risk_score: 0.05, risk_level: "low", missing_doc_types: [], evidence: [] })];
const SESSION: InterviewSession = {
  id: "sess_1", risk_id: "risk_1", topic: "Payment gateway", status: "in_progress", created_at: "2026-10-16T09:00:00Z", completed_at: null,
  questions: [{ id: "q1", text: "What do you check first when the gateway times out?", rationale: "Tickets show restarts", evidence: [cite()], answered: true, follow_up_of: null }],
  answers: [{ id: "a1", question_id: "q1", text: "SECRET DRAFT ANSWER", answered_at: "2026-10-16T10:00:00Z" }],
};
const DOC: GeneratedDoc = {
  id: "doc_1", type: "runbook", title: "Runbook: Payment gateway", topic: "Payment gateway",
  markdown: "# Runbook\n\n## Steps\n- Restart the pod [1]\n- Check **timeouts**\n\n## Sources\n1. INC-204", citations: [cite()],
  generated_at: "2026-10-17T08:00:00Z", model: "gpt-4o", open_questions: ["Who approves a failover?"],
};
const DEPARTURE: Departure = {
  id: "dep_1", person_name: "Ravi", source: "out_of_office", starts_at: "2026-10-20T00:00:00Z", ends_at: null,
  detected_at: "2026-10-17T00:00:00Z", status: "completed", session_ids: ["sess_1"], handover_doc_id: "doc_2",
  handover_url: "https://sp.example/handover.docx", error: null,
  at_risk: [{ risk_id: "risk_1", topic: "Payment gateway", share: 0.9, risk_score: 0.81, risk_level: "high", coverage: 0.1, risk_after: 0.2, level_after: "low" }],
};

function mockApi() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const path = new URL(url).pathname;
    const body = ({
      "/dashboard/hierarchy": TREE, "/dashboard/summary": makeSummary(), "/risks": { items: RISKS, total: 2, computed_at: "2026-10-17T12:00:00Z" },
      "/interviews": [SESSION], "/artifacts": [DOC], "/departures": [DEPARTURE],
    } as Record<string, unknown>)[path];
    return { ok: !!body, status: body ? 200 : 404, statusText: "x", json: async () => body ?? { detail: "not found" } } as Response;
  }));
}

beforeEach(() => { sessionStorage.clear(); localStorage.clear(); window.history.replaceState({}, "", "/"); });
afterEach(() => vi.unstubAllGlobals());

describe("Knowledge risks tab", () => {
  it("shows per-topic detail with evidence, interview progress and documents, but no owner names", async () => {
    mockApi();
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/?tab=risks");
    render(<App />);
    expect(await screen.findByText("Topics at high risk")).toBeInTheDocument();
    expect(screen.queryByRole("search")).not.toBeInTheDocument(); // scope/period filters do not apply here
    const table = screen.getByText("All topics").closest("figure") as HTMLElement;
    await user.click(within(table).getByRole("button", { name: "Payment gateway" }));
    const detail = screen.getByRole("region", { name: "Details for Payment gateway" });
    expect(within(detail).getByText("INC-204 gateway outage")).toHaveAttribute("href", "https://sp.example/doc.docx");
    expect(within(detail).getByText("What do you check first when the gateway times out?")).toBeInTheDocument();
    expect(within(detail).queryByText("SECRET DRAFT ANSWER")).not.toBeInTheDocument(); // drafts stay private
    expect(screen.queryByText(/person_ravi|Ravi/)).not.toBeInTheDocument(); // no owner ids, and the summary that names them is not shown
    await user.click(within(detail).getByRole("button", { name: "Runbook: Payment gateway" }));
    expect(await screen.findByRole("region", { name: "Runbook: Payment gateway" })).toBeInTheDocument();
  });

  it("deep-links to one risk", async () => {
    mockApi();
    window.history.replaceState({}, "", "/?tab=risks&risk=risk_1");
    render(<App />);
    expect(await screen.findByRole("region", { name: "Details for Payment gateway" })).toBeInTheDocument();
  });
});

describe("Interviews and documents tabs", () => {
  it("lists handovers with risk before and after", async () => {
    mockApi();
    window.history.replaceState({}, "", "/?tab=interviews");
    render(<App />);
    const row = (await screen.findByText("Ravi")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Handed over")).toBeInTheDocument();
    expect(within(row).getByText("0.20")).toBeInTheDocument();
    expect(within(row).getByText("Open")).toHaveAttribute("href", "https://sp.example/handover.docx");
  });

  it("renders a document with linked citations and without the duplicate Sources section", async () => {
    mockApi();
    window.history.replaceState({}, "", "/?tab=documents&doc=doc_1");
    render(<App />);
    const doc = await screen.findByRole("region", { name: "Runbook: Payment gateway" });
    expect(within(doc).getByRole("link", { name: "[1]" })).toHaveAttribute("href", "#cite-doc_1-1");
    expect(within(doc).getByText("Who approves a failover?")).toBeInTheDocument();
    expect(within(doc).getAllByText("Sources")).toHaveLength(1);
  });
});

describe("Markdown", () => {
  it("never renders HTML from the text", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> **bold**'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("bold").tagName).toBe("B");
  });
});
