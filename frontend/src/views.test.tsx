import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { ReadinessMeter } from "./components/ReadinessMeter";
import { CONSENTS, EVENTS, TREE, makeSummary } from "./test-fixtures";
import type { OnboardingSummary } from "./types";
import { Onboarding } from "./views/Onboarding";
import { Overview } from "./views/Overview";
import { Sources } from "./views/Sources";

const onboarding = (over: Partial<OnboardingSummary> = {}) => makeSummary({ onboarding: { ...makeSummary().onboarding, ...over } });

describe("Sources view", () => {
  it("shows what happened to SharePoint files, with the numbers from the summary", () => {
    render(<Sources s={makeSummary()} />);
    const tile = (label: string) => screen.getByText(label).closest(".card") as HTMLElement;
    expect(within(tile("Files added")).getByText("10")).toBeInTheDocument();
    expect(within(tile("Files changed")).getByText("6")).toBeInTheDocument();
    expect(within(tile("Files removed")).getByText("1")).toBeInTheDocument();
    expect(within(tile("Not loaded")).getByText("2")).toBeInTheDocument();   // 1 failed + 1 skipped
    expect(within(tile("Not loaded")).getByText("1 failed · 1 skipped")).toBeInTheDocument();
    expect(within(tile("Sync runs")).getByText("20")).toBeInTheDocument();
    expect(screen.getByText("2 runs failed")).toBeInTheDocument();
  });

  it("lists recent syncs and file changes with their results and reasons", () => {
    render(<Sources s={makeSummary()} />);
    expect(screen.getByText("Release process.docx")).toBeInTheDocument();
    expect(screen.getByText("could not read the PDF")).toBeInTheDocument();
    expect(screen.getByText("Type not supported")).toBeInTheDocument();
    expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);            // result chips, with an icon and a label
    expect(screen.getByText("403 Forbidden")).toBeInTheDocument();             // a failed sync says why
    expect(screen.getByText("first start without sample data")).toBeInTheDocument();
    expect(screen.getByText(".docx")).toBeInTheDocument();                      // file types chart
  });

  it("never shows who edited a file", () => {
    const { container } = render(<Sources s={makeSummary()} />);
    expect(container.textContent).toMatch(/who edited them is not/);
    expect(container.textContent).not.toMatch(/modified_by|Modified by|Edited by/i);
  });

  it("explains that SharePoint is org-level when viewing an account or project", () => {
    render(<Sources s={makeSummary({ sources: null })} />);
    expect(screen.getByText(/belong to the shared knowledge base/)).toBeInTheDocument();
    expect(screen.queryByText("Files added")).not.toBeInTheDocument();
  });

  it("handles a period with no file activity", () => {
    const empty = makeSummary();
    empty.sources = { ...empty.sources!, files_added: 0, files_modified: 0, files_deleted: 0, files_failed: 0, files_skipped: 0, by_type: {}, recent_files: [], recent_syncs: [], rebuilds: [], sync_runs: 0, sync_failures: 0, last_sync: null, last_sync_outcome: null };
    render(<Sources s={empty} />);
    expect(screen.getByText("No sync in this period")).toBeInTheDocument();
    expect(screen.getByText("No syncs in this period.")).toBeInTheDocument();
    expect(screen.getByText("No file changes in this period.")).toBeInTheDocument();
    expect(screen.getByText("0 failed · 0 skipped")).toBeInTheDocument();
    expect(screen.getByText("None failed")).toBeInTheDocument();
  });
});

describe("Onboarding view", () => {
  it("shows the funnel, learning requests and knowledge gaps as counts", () => {
    render(<Onboarding s={makeSummary()} />);
    for (const stage of ["Assigned", "Started learning", "Assessment started", "Completed", "Result shared"]) {
      expect(screen.getAllByText(stage).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("Questions")).toBeInTheDocument();
    expect(screen.getByText("Runbook requests")).toBeInTheDocument();
    const gaps = screen.getByText("Knowledge gaps").closest(".card") as HTMLElement;
    expect(within(gaps).getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Assignment, evaluation or delivery failed")).toBeInTheDocument();
  });

  it("shows the readiness spread and average only for a big enough group", () => {
    render(<Onboarding s={makeSummary()} />);
    expect(screen.getByText("Average score 64 out of 100")).toBeInTheDocument();
    for (const label of ["Ready", "Almost ready", "Needs more time"]) expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Onboarding readiness across the group: 1 ready, 2 almost ready, 1 needs more time/ })).toBeInTheDocument();
  });

  it("hides the spread for a small group and says why", () => {
    render(<Onboarding s={onboarding({ completed: 1, readiness: null, average_overall: null, suppressed: true })} />);
    expect(screen.getByText(/Fewer than 3 assessments are complete in this scope/)).toBeInTheDocument();
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
    expect(screen.queryByText(/Average score/)).not.toBeInTheDocument();
  });

  it("says nothing was completed rather than showing an empty chart", () => {
    render(<Onboarding s={onboarding({ completed: 0, assessments_started: 0, results_posted: 0, readiness: null, average_overall: null })} />);
    expect(screen.getByText("No assessments have been completed in this period.")).toBeInTheDocument();
  });

  it("invites the first assessment when none exist", () => {
    render(<Onboarding s={onboarding({ assigned: 0, started_learning: 0, assessments_started: 0, completed: 0, results_posted: 0, failed: 0, learning_questions: 0, learning_by_kind: {}, readiness: null, average_overall: null })} />);
    expect(screen.getByText(/No onboarding assessments in this period/)).toBeInTheDocument();
    expect(screen.getByText(/@LegacyLens assign @Name/)).toBeInTheDocument();
  });

  it("states the safeguards and names no one", () => {
    const { container } = render(<Onboarding s={makeSummary()} />);
    expect(container.textContent).toMatch(/sees their own result first/);
    expect(container.textContent).toMatch(/no names, no individual scores/);
    expect(container.textContent).toMatch(/at least 3/);
  });
});

describe("ReadinessMeter", () => {
  it("lists every level with an icon, a label and a count", () => {
    const { container } = render(<ReadinessMeter counts={{ ready: 2, almost: 0, needs_more_time: 1 }} />);
    expect(container.querySelectorAll(".meter-legend__row svg")).toHaveLength(3);
    expect(container.querySelectorAll(".meter__seg")).toHaveLength(2);           // the empty level has no segment but keeps its row
    expect(screen.getByText("Almost ready")).toBeInTheDocument();
  });
});

describe("Overview tiles for the new sources", () => {
  it("shows files synced (org) and onboarding completed", () => {
    render(<Overview s={makeSummary()} />);
    const files = screen.getByText("Files synced from SharePoint").closest(".card") as HTMLElement;
    expect(within(files).getByText("16")).toBeInTheDocument();                   // 10 added + 6 changed
    expect(within(files).getByText("1 removed · 1 failed")).toBeInTheDocument();
    const onb = screen.getByText("Onboarding assessments completed").closest(".card") as HTMLElement;
    expect(within(onb).getByText("4")).toBeInTheDocument();
    expect(within(onb).getByText("5 assigned")).toBeInTheDocument();
  });

  it("omits the SharePoint tile below the org level but keeps onboarding", () => {
    render(<Overview s={makeSummary({ sources: null, scope: TREE.children[0].children[0] })} />);
    expect(screen.queryByText("Files synced from SharePoint")).not.toBeInTheDocument();
    expect(screen.getByText("Onboarding assessments completed")).toBeInTheDocument();
  });
});

describe("tabs and the consent register", () => {
  function mockApi(summary = makeSummary()) {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const path = new URL(url).pathname;
      const body = path === "/dashboard/hierarchy" ? TREE : path === "/dashboard/summary" ? summary : path === "/dashboard/events" ? EVENTS : CONSENTS;
      return { ok: true, status: 200, statusText: "ok", json: async () => body } as Response;
    }));
  }
  beforeEach(() => { sessionStorage.clear(); localStorage.clear(); window.history.replaceState({}, "", "/"); });
  afterEach(() => vi.unstubAllGlobals());

  it("has a tab for knowledge sources and one for onboarding", async () => {
    mockApi();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Conversations allowed");
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "Overview", "Knowledge risks", "Interviews and handovers", "Documents", "Knowledge sources", "Onboarding", "Consent and data", "Audit trail",
    ]);
    await user.click(screen.getByRole("tab", { name: "Knowledge sources" }));
    expect(await screen.findByText("Recent file changes")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Onboarding" }));
    expect(await screen.findByText("Readiness across the group")).toBeInTheDocument();
  });

  it("opens the new tabs by deep link", async () => {
    mockApi();
    window.history.replaceState({}, "", "/?tab=sources");
    render(<App />);
    expect(await screen.findByText("Files synced per day")).toBeInTheDocument();
  });

  it("labels a SharePoint site in the consent register and names who configured it", async () => {
    mockApi();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Conversations allowed");
    await user.click(screen.getByRole("tab", { name: "Consent and data" }));
    const row = (await screen.findByText("Payments Platform SharePoint")).closest("tr") as HTMLElement;
    expect(within(row).getByText(/SharePoint site/)).toBeInTheDocument();                       // the type label, not "channel"
    expect(within(row).getByText("admin configuration (SHAREPOINT_SITE_URL)")).toBeInTheDocument();
    expect(within(row).getByText("Shared knowledge source")).toBeInTheDocument();
  });
});
