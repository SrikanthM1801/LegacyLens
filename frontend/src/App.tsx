import { useEffect, useState } from "react";
import { ApiError, api } from "./api";
import { AdminGate } from "./components/AdminGate";
import { Banner } from "./components/Banner";
import { DateRange, ScopePicker } from "./components/Filters";
import { SourceBadge } from "./components/SourceBadge";
import { useAdminKey, useAsync } from "./hooks";
import type { Scope } from "./types";
import { AuditTrail } from "./views/AuditTrail";
import { Consent } from "./views/Consent";
import { Documents } from "./views/Documents";
import { Interviews } from "./views/Interviews";
import { Onboarding } from "./views/Onboarding";
import { Risks } from "./views/Risks";
import { Sources } from "./views/Sources";
import { Overview } from "./views/Overview";

type Tab = "overview" | "risks" | "interviews" | "documents" | "sources" | "onboarding" | "consent" | "audit";
type Theme = "system" | "light" | "dark";
const TABS: [Tab, string][] = [
  ["overview", "Overview"], ["risks", "Knowledge risks"], ["interviews", "Interviews and handovers"],
  ["documents", "Documents"], ["sources", "Knowledge sources"], ["onboarding", "Onboarding"],
  ["consent", "Consent and data"], ["audit", "Audit trail"],
];
const THEME_KEY = "legacylens.theme";
const ORG: Scope = { level: "org", id: "org" };
/** Tabs over the live knowledge base: the scope and period filters do not apply to them. */
const KNOWLEDGE_TABS = new Set<Tab>(["risks", "interviews", "documents"]);

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const asked = new URLSearchParams(window.location.search).get("theme"); // ?theme=light|dark wins (a Teams tab can pass its theme)
    if (asked === "light" || asked === "dark") return asked;
    try { return (localStorage.getItem(THEME_KEY) as Theme) || "system"; } catch { return "system"; }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* storage blocked: theme lasts for this view */ }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "system" ? "light" : t === "light" ? "dark" : "system"))];
}

export function App() {
  const [adminKey, setAdminKey] = useAdminKey();
  const [scope, setScope] = useState<Scope>(ORG);
  const [days, setDays] = useState(30);
  const [tab, setTab] = useState<Tab>(() => {
    const t = new URLSearchParams(window.location.search).get("tab"); // deep link, e.g. ?tab=audit (also for a Teams tab)
    return TABS.some(([id]) => id === t) ? (t as Tab) : "overview";
  });
  const [theme, cycleTheme] = useTheme();
  const [docId, setDocId] = useState<string | null>(() => new URLSearchParams(window.location.search).get("doc"));
  const openDoc = (id: string) => { setDocId(id); setTab("documents"); };
  const knowledgeTab = KNOWLEDGE_TABS.has(tab);

  const tree = useAsync((s) => api.hierarchy(adminKey, s), [adminKey]);
  const summary = useAsync((s) => api.summary(scope, days, adminKey, s), [scope.level, scope.id, days, adminKey]);

  const failure = tree.error ?? summary.error;
  if (failure instanceof ApiError && failure.needsKey) {
    return (
      <div className="shell">
        <AdminGate onSubmit={setAdminKey} message={adminKey ? "That key was not accepted." : undefined} />
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <h1>LegacyLens</h1>
          <span>Transparency dashboard: what is read, stored and answered</span>
          {summary.data && <SourceBadge source={summary.data.data_source} />}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn--ghost" onClick={cycleTheme} aria-label={`Theme: ${theme}. Click to change.`}>Theme: {theme}</button>
          {adminKey && <button className="btn btn--ghost" onClick={() => setAdminKey("")}>Forget key</button>}
        </div>
      </header>

      {!knowledgeTab && <div className="filters" role="search">
        {tree.data && <ScopePicker root={tree.data} scope={scope} onChange={setScope} />}
        <span className="spacer" />
        <DateRange days={days} onChange={setDays} />
      </div>}

      <nav className="tabs" role="tablist" aria-label="Sections">
        {TABS.map(([id, label]) => (
          <button key={id} className="tab" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
        ))}
      </nav>

      <main className={summary.loading && summary.data && !knowledgeTab ? "dim" : undefined}>
        {failure && !knowledgeTab && !(failure instanceof ApiError && failure.needsKey) && (
          <Banner icon="alert" tone="critical">{failure.message}</Banner>
        )}
        {tab === "risks" && <Risks adminKey={adminKey} onOpenDoc={openDoc} />}
        {tab === "interviews" && <Interviews adminKey={adminKey} />}
        {tab === "documents" && <Documents adminKey={adminKey} openId={docId} onOpen={setDocId} />}
        {!knowledgeTab && !summary.data && summary.loading && <p className="empty" role="status">Loading…</p>}
        {summary.data && tab === "overview" && <Overview s={summary.data} />}
        {summary.data && tab === "sources" && <Sources s={summary.data} />}
        {summary.data && tab === "onboarding" && <Onboarding s={summary.data} />}
        {summary.data && tab === "consent" && <Consent summary={summary.data} scope={scope} adminKey={adminKey} />}
        {tab === "audit" && <AuditTrail scope={scope} days={days} adminKey={adminKey} />}
      </main>
    </div>
  );
}
