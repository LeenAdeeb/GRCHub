import type { Filters, Page, ReqDetail, Stat, Subdomain } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// The token lives in sessionStorage (cleared when the tab closes). If storage is blocked it is kept in memory only.
let memoryToken = "";
export function getToken(): string {
  try {
    return window.sessionStorage.getItem("grchub.token") ?? memoryToken;
  } catch {
    return memoryToken;
  }
}
export function setToken(t: string) {
  memoryToken = t;
  try {
    if (t) window.sessionStorage.setItem("grchub.token", t);
    else window.sessionStorage.removeItem("grchub.token");
  } catch {
    /* storage unavailable */
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function raw(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* body was not JSON */
    }
    if (res.status === 401 && token && !path.startsWith("/auth/login")) onUnauthorized();
    throw new ApiError(res.status, detail);
  }
  return res;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await raw(path, init)).json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export interface Me {
  id: number;
  email: string;
  role: string;
  org_id: number | null;
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Row = Record<string, any>;
export interface ListOf<T = Row> {
  total: number;
  items: T[];
}

export const api = {
  login: (email: string, password: string) =>
    request<{ access_token: string } & Me>("/auth/login", json("POST", { email, password })),
  me: () => request<Me>("/auth/me"),
  stats: () => request<Stat[]>("/stats"),
  subdomains: (framework: string) =>
    request<Subdomain[]>(`/subdomains${framework ? `?framework=${encodeURIComponent(framework)}` : ""}`),
  list: (f: Filters, offset: number, limit: number) => {
    const p = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (f.framework) p.set("framework", f.framework);
    if (f.status) p.set("status", f.status);
    if (f.subdomain) p.set("subdomain", f.subdomain);
    if (f.flagged) p.set("flagged", "true");
    if (f.q.trim()) p.set("q", f.q.trim());
    return request<Page>(`/requirements?${p}`);
  },
  get: (id: number) => request<ReqDetail>(`/requirements/${id}`),
  review: (id: number, body: { decision: "approved" | "rejected"; note: string | null }) =>
    request<ReqDetail>(`/requirements/${id}/review`, json("POST", body)),

  dashboard: () => request<Row>("/dashboard"),
  evidenceList: (status: string, requirementId?: number) => {
    const p = new URLSearchParams({ limit: "100" });
    if (status) p.set("status", status);
    if (requirementId) p.set("requirement_id", String(requirementId));
    return request<ListOf>(`/evidence?${p}`);
  },
  evidenceCreate: (form: FormData) => request<Row>("/evidence", { method: "POST", body: form }),
  evidenceAttach: (id: number, form: FormData) => request<Row>(`/evidence/${id}/attachment`, { method: "POST", body: form }),
  evidenceTransition: (id: number, to: string, comment: string | null) =>
    request<Row>(`/evidence/${id}/transition`, json("POST", { to, comment })),
  evidenceDownload: async (id: number, name: string) => {
    const blob = await (await raw(`/evidence/${id}/download`)).blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  },
  assessments: () => request<ListOf>("/assessments?limit=200"),
  assessmentHistory: (requirementId: number) => request<Row[]>(`/assessments/history/${requirementId}`),
  assessmentCreate: (body: Row) => request<Row>("/assessments", json("POST", body)),
  mappings: (status: string) => request<ListOf>(`/mappings?limit=200${status ? `&status=${status}` : ""}`),
  mappingCreate: (body: Row) => request<Row>("/mappings", json("POST", body)),
  mappingDecide: (id: number, decision: string, note: string | null) =>
    request<Row>(`/mappings/${id}/decision`, json("POST", { decision, note })),
  changes: (framework: string, from: string, to: string) =>
    request<Row>(`/changes?framework=${encodeURIComponent(framework)}&from_version=${encodeURIComponent(from)}&to_version=${encodeURIComponent(to)}`),
  orgs: () => request<Row[]>("/organizations"),
  orgCreate: (name: string) => request<Row>("/organizations", json("POST", { name })),
  users: () => request<Row[]>("/users"),
  userCreate: (body: Row) => request<Row>("/users", json("POST", body)),
  userActive: (id: number, is_active: boolean) => request<Row>(`/users/${id}/active`, json("PATCH", { is_active })),
  userResetPassword: (id: number, new_password: string) => request<Row>(`/users/${id}/password`, json("PUT", { new_password })),
  changePassword: (current_password: string, new_password: string) =>
    request<Row>("/auth/change-password", json("POST", { current_password, new_password })),
  sugConfig: () => request<Row>("/suggestions/config"),
  sugGenerate: (body: Row) => request<Row>("/suggestions/generate", json("POST", body)),
  sugList: (p: Record<string, string>) => request<ListOf>(`/suggestions?${new URLSearchParams(p)}`),
  sugEstimate: (s: string, t: string, provider: string) =>
    request<Row>(`/suggestions/llm/estimate?source_framework=${encodeURIComponent(s)}&target_framework=${encodeURIComponent(t)}${provider ? `&provider=${provider}` : ""}`),
  sugRun: (body: Row) => request<Row>("/suggestions/llm/run", json("POST", body)),
  sugJob: (id: string) => request<Row>(`/suggestions/llm/jobs/${id}`),
  sugDismiss: (id: number, note: string | null) => request<Row>(`/suggestions/${id}/dismiss`, json("POST", { note })),
  sugPropose: (id: number, relationship: string, justification: string) =>
    request<Row>(`/suggestions/${id}/propose`, json("POST", { relationship, justification })),
  maturity: () => request<Row>("/maturity"),
  maturityReview: (kind: "levels" | "sections", id: number, decision: string, note: string | null) =>
    request<Row>(`/maturity/${kind}/${id}/review`, json("POST", { decision, note })),
};
