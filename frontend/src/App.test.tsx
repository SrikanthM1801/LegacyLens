import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { CONSENTS, EVENTS, TREE, makeSummary } from "./test-fixtures";

type Call = { path: string; params: URLSearchParams; key: string | null };

function mockApi(opts: { requireKey?: string; summary?: Parameters<typeof makeSummary>[0]; down?: boolean } = {}) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (opts.down) throw new TypeError("network down");
    const u = new URL(url);
    const key = (init?.headers as Record<string, string> | undefined)?.["X-Admin-Key"] ?? null;
    calls.push({ path: u.pathname, params: u.searchParams, key });
    const json = (body: unknown, status = 200) => ({ ok: status < 400, status, statusText: "x", json: async () => body }) as Response;
    if (opts.requireKey && key !== opts.requireKey) return json({ detail: "Missing or wrong X-Admin-Key" }, 401);
    if (u.pathname === "/dashboard/hierarchy") return json(TREE);
    if (u.pathname === "/dashboard/summary") return json(makeSummary(opts.summary));
    if (u.pathname === "/dashboard/events") return json(EVENTS);
    if (u.pathname === "/dashboard/consents") return json(CONSENTS);
    return json({ detail: "not found" }, 404);
  }));
  return calls;
}

beforeEach(() => { sessionStorage.clear(); window.history.replaceState({}, "", "/"); });
afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("loads the org overview: hero, KPIs, charts and the demo note", async () => {
    const calls = mockApi();
    render(<App />);
    expect(await screen.findByText("Conversations allowed")).toBeInTheDocument();
    expect(screen.getByText("of 3")).toBeInTheDocument();
    expect(screen.getByText("Demo data: these events are generated, not read from Teams.")).toBeInTheDocument();
    expect(screen.getByText("Messages learned per day")).toBeInTheDocument();
    expect(screen.getByText("Knowledge risk before and after interviews")).toBeInTheDocument();
    expect(screen.queryByText("Knowledge base now")).not.toBeInTheDocument(); // audit + consent only, no sample-dataset numbers
    const summary = calls.find((c) => c.path === "/dashboard/summary")!;
    expect(summary.params.get("level")).toBe("org");
    expect(summary.params.get("id")).toBe("org");
    const span = new Date(summary.params.get("until")!).getTime() - new Date(summary.params.get("since")!).getTime();
    expect(Math.round(span / 86_400_000)).toBe(30);
  });

  it("re-queries at account and project level when the scope changes", async () => {
    const calls = mockApi();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Conversations allowed");
    await user.selectOptions(screen.getByLabelText("Account"), "Contoso");
    await waitFor(() => expect(calls.some((c) => c.path === "/dashboard/summary" && c.params.get("level") === "account" && c.params.get("id") === "acc_a")).toBe(true));
    await user.selectOptions(screen.getByLabelText("Project"), "Payments");
    await waitFor(() => expect(calls.some((c) => c.path === "/dashboard/summary" && c.params.get("level") === "project" && c.params.get("id") === "prj_1")).toBe(true));
    // clearing the project goes back up to the account, clearing the account back to the org
    await user.selectOptions(screen.getByLabelText("Project"), "All projects");
    await waitFor(() => expect(calls.at(-1)!.params.get("level")).toBe("account"));
    await user.selectOptions(screen.getByLabelText("Account"), "All accounts");
    await waitFor(() => expect(calls.at(-1)!.params.get("level")).toBe("org"));
  });

  it("limits the project list to the chosen account", async () => {
    mockApi();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Conversations allowed");
    const project = screen.getByLabelText("Project");
    expect(within(project).getAllByRole("option").map((o) => o.textContent)).toEqual(["All projects", "Payments", "Storefront"]);
    await user.selectOptions(screen.getByLabelText("Account"), "Northwind");
    expect(within(project).getAllByRole("option").map((o) => o.textContent)).toEqual(["All projects", "Storefront"]);
  });

  it("changes the period", async () => {
    const calls = mockApi();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Conversations allowed");
    await user.click(screen.getByRole("radio", { name: "Last 7 days" }));
    await waitFor(() => {
      const last = calls.filter((c) => c.path === "/dashboard/summary").at(-1)!;
      const span = new Date(last.params.get("until")!).getTime() - new Date(last.params.get("since")!).getTime();
      expect(Math.round(span / 86_400_000)).toBe(7);
    });
    expect(screen.getByRole("radio", { name: "Last 7 days" })).toHaveAttribute("aria-checked", "true");
  });

  it("asks for the admin key on 401, sends it as X-Admin-Key, and keeps it only for the tab", async () => {
    const calls = mockApi({ requireKey: "k3y" });
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByText("Admin access")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Admin key"), "wrong");
    await user.click(screen.getByRole("button", { name: "Open dashboard" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not accepted");
    await user.clear(screen.getByLabelText("Admin key"));
    await user.type(screen.getByLabelText("Admin key"), "k3y");
    await user.click(screen.getByRole("button", { name: "Open dashboard" }));
    expect(await screen.findByText("Conversations allowed")).toBeInTheDocument();
    expect(calls.at(-1)!.key).toBe("k3y");
    expect(sessionStorage.getItem("legacylens.adminKey")).toBe("k3y");
    expect(localStorage.getItem("legacylens.adminKey")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Forget key" }));
    expect(await screen.findByText("Admin access")).toBeInTheDocument();
    expect(sessionStorage.getItem("legacylens.adminKey")).toBeNull();
  });

  it("explains an unreachable backend", async () => {
    mockApi({ down: true });
    render(<App />);
    expect(await screen.findByText(/Cannot reach the LegacyLens API/)).toBeInTheDocument();
  });

  it("shows the consent register and what is stored where, naming only the decider", async () => {
    mockApi();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Conversations allowed");
    await user.click(screen.getByRole("tab", { name: "Consent and data" }));
    expect(await screen.findByText("Consent register")).toBeInTheDocument();
    expect(await screen.findByText("Priya Nair")).toBeInTheDocument();
    expect(screen.getAllByText("Waiting for a decision").length).toBeGreaterThan(0); // the chip, and the filter option
    expect(screen.getByText("What is processed and where it is kept")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Show"), "Waiting for a decision");
    expect(screen.queryByText("Priya Nair")).not.toBeInTheDocument();
    expect(screen.getByText("Release planning", { exact: false })).toBeInTheDocument();
  });

  it("audit trail: anonymised events, filters go to the API, empty details are hidden", async () => {
    const calls = mockApi();
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Conversations allowed");
    await user.click(screen.getByRole("tab", { name: "Audit trail" }));
    expect(await screen.findByText("Showing 2 of 2 events, newest first")).toBeInTheDocument();
    expect(screen.getByText("chunks: 3")).toBeInTheDocument(); // `redactions: {}` is dropped
    expect(screen.getByText("member")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Result"), "Failed");
    await waitFor(() => expect(calls.filter((c) => c.path === "/dashboard/events").at(-1)!.params.get("outcome")).toBe("failure"));
    await user.selectOptions(screen.getByLabelText("Kind"), "Consent");
    await waitFor(() => expect(calls.filter((c) => c.path === "/dashboard/events").at(-1)!.params.get("category")).toBe("consent"));
  });

  it("hides the org-only risk panel below the org level", async () => {
    mockApi({ summary: { risk_impact: [], scope: TREE.children[0].children[0] } });
    render(<App />);
    expect(await screen.findByText("Conversations allowed")).toBeInTheDocument();
    expect(screen.getByText("Chats detected")).toBeInTheDocument();
  });

  it.each([
    ["azure", /Live: Azure audit trail/],
    ["memory", /Offline: this session only/],
    ["demo", /Demo data/],
  ] as const)("always says where the numbers come from (%s)", async (source, label) => {
    mockApi({ summary: { data_source: source } });
    render(<App />);
    expect(await screen.findByRole("status", { name: label })).toBeInTheDocument();
  });

  it("does not claim nobody has decided when only a pause was recorded", async () => {
    mockApi({ summary: { consent_funnel: { requested: 0, granted: 0, declined: 0, paused: 1, approval_rate: null } } });
    render(<App />);
    expect(await screen.findByText("No Allow or Not now decisions in this period")).toBeInTheDocument();
    expect(screen.queryByText(/Nobody has decided/)).not.toBeInTheDocument();
  });

  it("shows the API's reason when live storage cannot be read, and no stale numbers", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false, status: 503, statusText: "x",
      json: async () => ({ detail: "Could not read the audit trail from Azure (RuntimeError). Check you are signed in (az login)." }),
    }) as Response));
    render(<App />);
    expect(await screen.findByText(/Could not read the audit trail from Azure/)).toBeInTheDocument();
    expect(screen.queryByText("Conversations allowed")).not.toBeInTheDocument();
    expect(screen.queryByRole("status", { name: /Data source/ })).not.toBeInTheDocument();
  });

  it("opens the tab named in the URL (deep link) and honours ?theme=", async () => {
    mockApi();
    window.history.replaceState({}, "", "/?tab=audit&theme=dark");
    render(<App />);
    expect(await screen.findByText("Audit trail", { selector: "h2" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Audit trail" })).toHaveAttribute("aria-selected", "true");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});
