import { useState } from "react";
import { api } from "./api";
import type { Me, Row } from "./api";
import { useLoad } from "./Org";

const ROLE_HELP: Record<string, string> = {
  platform_admin: "Platform admin: full access to regulations, mappings, organizations and all accounts",
  reviewer: "Reviewer: approves requirements, maturity levels and mappings",
  org_admin: "Organization admin: manages the organization's users, evidence and assessments",
  analyst: "Analyst: adds evidence",
  assessor: "Assessor: reviews evidence and records assessments",
  viewer: "Viewer: read-only",
};
const PLATFORM = ["platform_admin", "reviewer"];
const ORG = ["org_admin", "analyst", "assessor", "viewer"];

const Err = ({ msg }: { msg: string | null }) => (msg ? <p className="error" role="alert">{msg}</p> : null);

function Organizations({ onChange }: { onChange: () => void }) {
  const [orgs, err, reload] = useLoad(() => api.orgs(), []);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  async function create(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.orgCreate(name);
      setName(""); setMsg(null); reload(); onChange();
    } catch (x) { setMsg((x as Error).message); }
  }
  return (
    <section>
      <h3>Organizations</h3>
      <Err msg={err ?? msg} />
      <ul>{orgs?.map((o) => <li key={o.id}>{o.name} <span className="muted">(id {o.id})</span></li>)}</ul>
      {orgs && orgs.length === 0 && <p className="empty">No organization yet. Add the first one below.</p>}
      <form className="two" onSubmit={create}>
        <label>New organization<input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} required /></label>
        <div className="actions"><button className="approve">Add organization</button></div>
      </form>
    </section>
  );
}

export default function UsersTab({ me }: { me: Me }) {
  const isPlatform = me.role === "platform_admin";
  const [users, err, reload] = useLoad(() => api.users(), []);
  const [orgs, , reloadOrgs] = useLoad(() => (isPlatform ? api.orgs() : Promise.resolve([] as Row[])), []);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState(isPlatform ? "reviewer" : "analyst");
  const [orgId, setOrgId] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [resetFor, setResetFor] = useState<number | null>(null);
  const [newPw, setNewPw] = useState("");

  const roles = isPlatform ? [...PLATFORM, ...ORG] : ORG;
  const needsOrg = ORG.includes(role) && isPlatform;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setOk(null);
    try {
      await api.userCreate({ email, password, role, org_id: needsOrg ? Number(orgId) : null });
      setOk(`Account created for ${email}. Share the password with them through a safe channel and ask them to change it.`);
      setEmail(""); setPassword(""); setMsg(null); reload();
    } catch (x) { setMsg((x as Error).message); }
  }
  async function toggle(u: Row) {
    try { await api.userActive(u.id, !u.is_active); setMsg(null); reload(); } catch (x) { setMsg((x as Error).message); }
  }
  async function reset(id: number) {
    try {
      await api.userResetPassword(id, newPw);
      setResetFor(null); setNewPw(""); setMsg(null); setOk("Password reset. Their current sessions stop working.");
    } catch (x) { setMsg((x as Error).message); }
  }

  return (
    <div>
      <h2 className="page-title">{isPlatform ? "Organizations and users" : "Users in your organization"}</h2>
      {isPlatform && <Organizations onChange={reloadOrgs} />}
      <h3>Users</h3>
      <Err msg={err ?? msg} />
      {ok && <p className="note" role="status">{ok}</p>}
      <table>
        <thead><tr><th>Email</th><th>Role</th>{isPlatform && <th>Organization</th>}<th>Status</th><th /></tr></thead>
        <tbody>
          {users?.map((u) => (
            <tr key={u.id}>
              <td>{u.email}</td><td>{u.role}</td>{isPlatform && <td>{u.org_name ?? "—"}</td>}
              <td>{u.is_active ? "active" : <span className="flag">disabled</span>}</td>
              <td className="actions">
                {u.id !== me.id && (<>
                  <button onClick={() => toggle(u)}>{u.is_active ? "Disable" : "Enable"}</button>
                  {resetFor === u.id ? (
                    <span className="inline">
                      <input type="password" placeholder="New password (12+)" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" />
                      <button className="approve" onClick={() => reset(u.id)}>Set</button>
                      <button onClick={() => { setResetFor(null); setNewPw(""); }}>Cancel</button>
                    </span>
                  ) : <button onClick={() => { setResetFor(u.id); setNewPw(""); }}>Reset password</button>}
                </>)}
                {u.id === me.id && <span className="muted">you</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form className="decide" onSubmit={create}>
        <h3>Add a user</h3>
        <div className="two">
          <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label>Initial password (12+ characters)<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required minLength={12} /></label>
        </div>
        <div className="two">
          <label>Role
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              {roles.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          {needsOrg && (
            <label>Organization
              <select value={orgId} onChange={(e) => setOrgId(e.target.value)} required>
                <option value="" />
                {orgs?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </label>
          )}
        </div>
        <p className="muted">{ROLE_HELP[role]}</p>
        <div className="actions"><button className="approve">Create user</button></div>
      </form>
    </div>
  );
}

export function AccountTab({ onChanged }: { onChanged: () => void }) {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.changePassword(cur, next);
      onChanged();            // the old token is no longer valid: go back to the sign-in screen
    } catch (x) { setMsg((x as Error).message); }
  }
  return (
    <form className="decide" onSubmit={submit} style={{ maxWidth: 420 }}>
      <h2 className="page-title">Change my password</h2>
      <label>Current password<input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" required /></label>
      <label>New password (12+ characters)<input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" required minLength={12} /></label>
      <Err msg={msg} />
      <p className="muted">You will be signed out and must sign in again with the new password.</p>
      <div className="actions"><button className="approve">Change password</button></div>
    </form>
  );
}
