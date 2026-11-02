import { useEffect, useState } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "./api";
import type { Me } from "./api";
import Login from "./Login";
import Review from "./Review";
import UsersTab, { AccountTab } from "./Users";
import { AssessmentsTab, ChangesTab, Dashboard, EvidenceTab, MappingsTab, MaturityTab } from "./Org";

type Tab = "users" | "account" | "dashboard" | "review" | "evidence" | "assessments" | "mappings" | "maturity" | "changes";
const TABS: { id: Tab; label: string; roles?: string[] }[] = [
  { id: "dashboard", label: "Dashboard", roles: ["org_admin", "analyst", "assessor", "viewer"] },
  { id: "review", label: "Requirement review" },
  { id: "maturity", label: "Maturity model" },
  { id: "mappings", label: "Mappings" },
  { id: "evidence", label: "Evidence", roles: ["org_admin", "analyst", "assessor", "viewer"] },
  { id: "assessments", label: "Assessments", roles: ["org_admin", "analyst", "assessor", "viewer"] },
  { id: "changes", label: "Changes" },
  { id: "users", label: "Users", roles: ["platform_admin", "org_admin"] },
  { id: "account", label: "My password" },
];
const ROLE_LABEL: Record<string, string> = {
  platform_admin: "Platform admin", reviewer: "Reviewer", org_admin: "Organization admin",
  analyst: "Analyst", assessor: "Assessor", viewer: "Viewer",
};

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checked, setChecked] = useState(false);
  const [tab, setTab] = useState<Tab | null>(null);

  function logout() {
    setToken("");
    setMe(null);
    setTab(null);
  }

  useEffect(() => {
    setUnauthorizedHandler(logout);
    if (!getToken()) return setChecked(true);
    api.me().then(setMe).catch(() => setToken("")).finally(() => setChecked(true));
  }, []);

  if (!checked) return <p className="empty">Loading…</p>;
  if (!me) return <div className="app"><Login onLogin={setMe} /></div>;

  const tabs = TABS.filter((t) => !t.roles || t.roles.includes(me.role));
  const current = tab && tabs.some((t) => t.id === tab) ? tab : tabs[0].id;
  const canReview = ["reviewer", "platform_admin"].includes(me.role);

  return (
    <div className="app">
      <header className="topbar">
        <h1>GRCHub</h1>
        <nav className="tabs" aria-label="Sections">
          {tabs.map((t) => (
            <button key={t.id} className={t.id === current ? "tab active" : "tab"} onClick={() => setTab(t.id)}>{t.label}</button>
          ))}
        </nav>
        <span className="who">{me.email} · {ROLE_LABEL[me.role] ?? me.role} <button onClick={logout}>Sign out</button></span>
      </header>
      {current === "dashboard" && <Dashboard />}
      {current === "review" && <Review canReview={canReview} />}
      {current === "maturity" && <MaturityTab me={me} />}
      {current === "mappings" && <MappingsTab me={me} />}
      {current === "evidence" && <EvidenceTab me={me} />}
      {current === "assessments" && <AssessmentsTab me={me} />}
      {current === "changes" && <ChangesTab />}
      {current === "users" && <UsersTab me={me} />}
      {current === "account" && <AccountTab onChanged={logout} />}
    </div>
  );
}
