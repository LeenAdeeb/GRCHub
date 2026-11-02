import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { Me, Row } from "./api";
import { ReqPicker } from "./Pickers";
import type { Req } from "./types";
import Suggestions from "./Suggestions";

const fmt = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): [T | null, string | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    fn().then((d) => { setData(d); setErr(null); }).catch((e: Error) => setErr(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return [data, err, () => setN((x) => x + 1)];
}

const Err = ({ msg }: { msg: string | null }) => (msg ? <p className="error" role="alert">{msg}</p> : null);

// ------------------------------------------------------------------ dashboard
const pct = (x: number | null) => (x == null ? "—" : `${Math.round(x * 100)}%`);

function PostureBar({ p }: { p: Row }) {
  const total = p.applicable || 1;
  const parts = [
    ["demonstrated", p.demonstrated, "Demonstrated compliant"],
    ["revalidate", p.compliant_needs_revalidation, "Compliant, evidence needs renewal"],
    ["partial", p.partial, "Partially compliant"],
    ["noncompliant", p.non_compliant, "Non-compliant"],
    ["unassessed", p.not_assessed, "Not yet assessed"],
  ] as const;
  return (
    <>
      <div className="bar" role="img" aria-label={parts.map(([, n, l]) => `${n} ${l}`).join(", ")}>
        {parts.map(([k, n]) => <span key={k} className={`seg ${k}`} style={{ width: `${(n / total) * 100}%` }} />)}
      </div>
      <p className="legend">
        {parts.map(([k, n, l]) => <span key={k}><i className={`dot ${k}`} />{l}: <strong>{n}</strong></span>)}
        {p.not_applicable > 0 && <span>Not applicable (excluded): <strong>{p.not_applicable}</strong></span>}
      </p>
    </>
  );
}

export function Dashboard() {
  const [d, err] = useLoad(() => api.dashboard(), []);
  if (err) return <Err msg={err} />;
  if (!d) return <p className="empty">Loading…</p>;
  const ev = d.evidence;
  const t = d.thresholds;
  return (
    <div>
      <h2 className="page-title">Compliance posture</h2>
      <p className="muted">
        Compliance counts only when an assessor has recorded it <em>and</em> the evidence behind it is still accepted. Anything not yet
        assessed counts against the rate. Each framework is judged on its own; they are never averaged.
      </p>
      {d.frameworks.map((f: Row) => {
        const p = f.posture;
        return (
          <section key={f.framework_version_id} className="card">
            <h3>
              {f.framework} {f.version} <span className={`posture lvl-${p.level}`}>{p.label}</span>{" "}
              {p.provisional && <span className="flag">provisional</span>}
            </h3>
            <p className="big">
              {pct(p.demonstrated_rate)} <span className="muted">demonstrated compliant · {p.demonstrated} of {p.applicable} applicable requirements · {pct(p.assessed_share)} assessed</span>
            </p>
            {p.applicable > 0 && <PostureBar p={p} />}
            {p.reasons.map((r: string) => <p className="note" key={r}>{r}</p>)}
            {f.median_maturity != null && <p>Median maturity level recorded: <strong>{f.median_maturity}</strong> ({Object.entries(f.maturity_distribution).map(([l, n]) => `level ${l}: ${n}`).join(" · ")})</p>}
            {f.domains.length > 1 && (
              <details>
                <summary>By domain</summary>
                <table>
                  <thead><tr><th>Domain</th><th>Applicable</th><th>Demonstrated</th><th>Rate</th><th>Assessed</th><th>Non-compliant</th><th>Posture</th></tr></thead>
                  <tbody>
                    {f.domains.map((x: Row) => (
                      <tr key={x.domain}>
                        <td>{x.domain}{x.awaiting_review > 0 && <span className="muted"> ({x.awaiting_review} awaiting review)</span>}</td>
                        <td>{x.applicable}</td><td>{x.demonstrated}</td><td>{pct(x.demonstrated_rate)}</td><td>{pct(x.assessed_share)}</td>
                        <td>{x.non_compliant}</td><td><span className={`posture lvl-${x.level}`}>{x.label}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            )}
          </section>
        );
      })}

      <h3>Evidence needing attention</h3>
      <p>
        Submitted {ev.needs_attention.submitted} · Under review {ev.needs_attention.under_review} · Rejected {ev.needs_attention.rejected} · Expired {ev.needs_attention.expired}
      </p>
      <p className={ev.assessments_relying_on_invalid_evidence ? "note" : "muted"}>
        Assessments relying on evidence that is no longer accepted: <strong>{ev.assessments_relying_on_invalid_evidence}</strong>
      </p>
      {ev.due_soon.length > 0 && (
        <>
          <h3>Accepted evidence due for review within {t.evidence_due_soon_days} days</h3>
          <ul>{ev.due_soon.map((e: Row) => <li key={e.id}>{e.name} — {fmt(e.review_due_date)}</li>)}</ul>
        </>
      )}
      {d.top_gaps.length > 0 && (
        <>
          <h3>Non-compliant requirements</h3>
          <p>{d.top_gaps.map((g: Row) => `${g.framework} ${g.control_id}`).join(" · ")}</p>
        </>
      )}
      <details>
        <summary>How the posture is decided</summary>
        <p>
          Only reviewer-approved requirements are in scope; requirements still awaiting review make the result <em>provisional</em>.
          Applicable = approved minus those assessed as not applicable. Demonstrated = latest assessment is compliant and all cited
          evidence is still accepted. Rate = demonstrated ÷ applicable. Labels: {pct(t.substantially_compliant)}+ Substantially compliant,
          {" "}{pct(t.mostly_compliant)}+ Mostly compliant, {pct(t.partially_compliant)}+ Partially compliant, below that Largely non-compliant.
          If fewer than {pct(t.min_assessed_share)} of applicable requirements are assessed, no label is given. These thresholds are this
          tool's convention, not a regulatory standard, and the posture is an internal indicator, not a certification.
        </p>
      </details>
    </div>
  );
}

// ------------------------------------------------------------------ evidence
export function EvidenceTab({ me }: { me: Me }) {
  const [status, setStatus] = useState("");
  const [data, err, reload] = useLoad(() => api.evidenceList(status), [status]);
  const [msg, setMsg] = useState<string | null>(null);
  const [reqs, setReqs] = useState<Req[]>([]);
  const [busy, setBusy] = useState(false);
  const canSubmit = ["org_admin", "analyst"].includes(me.role);
  const canJudge = ["org_admin", "assessor"].includes(me.role);

  async function create(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const fd = new FormData(form);
    if (!(fd.get("file") as File)?.name) fd.delete("file");
    if (!fd.get("external_url")) fd.delete("external_url");
    if (!fd.get("review_due_date")) fd.delete("review_due_date");
    if (reqs.length) fd.set("requirement_ids", reqs.map((r) => r.id).join(","));
    setBusy(true);
    try {
      await api.evidenceCreate(fd);
      form.reset();
      setReqs([]);
      setMsg(null);
      reload();
    } catch (x) {
      setMsg((x as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function act(fn: () => Promise<unknown>) {
    try { await fn(); setMsg(null); reload(); } catch (x) { setMsg((x as Error).message); }
  }
  function transition(id: number, to: string) {
    let comment: string | null = null;
    if (to === "rejected") {
      comment = window.prompt("Reason for rejecting (required):");
      if (!comment) return;
    }
    act(() => api.evidenceTransition(id, to, comment));
  }

  return (
    <div>
      <h2 className="page-title">Evidence</h2>
      <label>Status
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All</option>
          {["pending", "submitted", "under_review", "accepted", "rejected", "expired"].map((s) => <option key={s}>{s}</option>)}
        </select>
      </label>
      <Err msg={err ?? msg} />
      <table>
        <thead><tr><th>Name</th><th>Status</th><th>File / link</th><th>Due</th><th>Comments</th><th /></tr></thead>
        <tbody>
          {data?.items.map((e) => (
            <tr key={e.id}>
              <td>{e.name}</td><td><span className={`badge ${e.status}`}>{e.status}</span></td>
              <td>
                {e.original_filename ? <button onClick={() => act(() => api.evidenceDownload(e.id, e.original_filename))}>{e.original_filename}</button>
                  : e.external_url ? <a href={e.external_url} target="_blank" rel="noopener noreferrer">link</a> : "—"}
              </td>
              <td>{fmt(e.review_due_date)}</td><td>{e.reviewer_comments ?? ""}</td>
              <td className="actions">
                {canJudge && e.status === "submitted" && <button onClick={() => transition(e.id, "under_review")}>Start review</button>}
                {canJudge && e.status === "under_review" && (<>
                  <button className="approve" onClick={() => transition(e.id, "accepted")}>Accept</button>
                  <button className="reject" onClick={() => transition(e.id, "rejected")}>Reject</button></>)}
                {canJudge && e.status === "accepted" && <button onClick={() => transition(e.id, "expired")}>Mark expired</button>}
                {canSubmit && ["pending", "rejected", "expired"].includes(e.status) && (
                  <label className="inline">Replace
                    <input type="file" onChange={(ev) => { const f = ev.target.files?.[0]; if (!f) return; const fd = new FormData(); fd.set("file", f); act(() => api.evidenceAttach(e.id, fd)); }} />
                  </label>)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && data.items.length === 0 && <p className="empty">No evidence yet.</p>}
      {canSubmit && (
        <form className="decide" onSubmit={create}>
          <h3>Add evidence</h3>
          <p className="muted">Allowed: PDF, PNG, JPG, DOCX, XLSX, PPTX, TXT, CSV up to 25 MB. Files are checked for type but not scanned for malware.</p>
          <div className="two">
            <label>Name<input name="name" required maxLength={300} /></label>
            <label>Review due date<input name="review_due_date" type="date" /></label>
          </div>
          <label>Description<textarea name="description" rows={2} /></label>
          <div className="two">
            <label>File<input name="file" type="file" /></label>
            <label>…or external URL<input name="external_url" type="url" /></label>
          </div>
          <ReqPicker label="Link to requirement" value={null} onChange={(r) => r && !reqs.some((x) => x.id === r.id) && setReqs([...reqs, r])} />
          {reqs.map((r) => (
            <span className="chip" key={r.id}>{r.framework} {r.control_id} <button type="button" onClick={() => setReqs(reqs.filter((x) => x.id !== r.id))} aria-label="Remove">×</button></span>
          ))}
          <div className="actions"><button className="approve" disabled={busy}>Submit evidence</button></div>
        </form>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ assessments
export function AssessmentsTab({ me }: { me: Me }) {
  const [data, err, reload] = useLoad(() => api.assessments(), []);
  const [req, setReq] = useState<Req | null>(null);
  const [result, setResult] = useState("non_compliant");
  const [maturity, setMaturity] = useState("");
  const [notes, setNotes] = useState("");
  const [accepted, setAccepted] = useState<Row[]>([]);
  const [chosen, setChosen] = useState<number[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [hist, setHist] = useState<Row[] | null>(null);
  const canJudge = ["org_admin", "assessor"].includes(me.role);

  useEffect(() => {
    setChosen([]);
    if (!req) return setAccepted([]);
    api.evidenceList("accepted", req.id).then((r) => setAccepted(r.items)).catch(() => setAccepted([]));
  }, [req]);

  async function save() {
    if (!req) return;
    try {
      await api.assessmentCreate({ requirement_id: req.id, result, maturity_level: maturity === "" ? null : Number(maturity), notes: notes || null, evidence_ids: chosen });
      setMsg(null); setReq(null); setNotes(""); setMaturity(""); reload();
    } catch (x) { setMsg((x as Error).message); }
  }

  return (
    <div>
      <h2 className="page-title">Assessments</h2>
      <p className="muted">Each save adds a new entry; earlier results stay in the history. Compliant and partially compliant need accepted evidence linked to the requirement.</p>
      <Err msg={err} />
      <table>
        <thead><tr><th>Framework</th><th>Control</th><th>Result</th><th>Maturity</th><th>Assessor</th><th>Date</th><th>Evidence</th><th /></tr></thead>
        <tbody>
          {data?.items.map((a) => (
            <tr key={a.id}>
              <td>{a.framework} {a.version}</td><td>{a.control_id}</td><td>{a.result}</td><td>{a.maturity_level ?? "—"}</td>
              <td>{a.assessor}</td><td>{fmt(a.assessed_at)}</td>
              <td>{a.evidence_ids.length === 0 ? "—" : a.evidence_still_valid ? `${a.evidence_ids.length} accepted` : <span className="flag">no longer valid</span>}</td>
              <td><button onClick={() => api.assessmentHistory(a.requirement_id).then(setHist)}>History</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && data.items.length === 0 && <p className="empty">No assessments yet.</p>}
      {hist && (
        <section>
          <h3>History <button onClick={() => setHist(null)}>Close</button></h3>
          <ul className="history">{hist.map((h) => <li key={h.id}>{h.result} · maturity {h.maturity_level ?? "—"} · {h.assessor} · {new Date(h.assessed_at).toLocaleString()}{h.notes ? ` — ${h.notes}` : ""}</li>)}</ul>
        </section>
      )}
      {canJudge && (
        <form className="decide" onSubmit={(e) => { e.preventDefault(); save(); }}>
          <h3>New assessment</h3>
          <ReqPicker label="Requirement" value={req} onChange={setReq} />
          {req && (<>
            <div className="two">
              <label>Result
                <select value={result} onChange={(e) => setResult(e.target.value)}>
                  {["compliant", "partially_compliant", "non_compliant", "not_applicable"].map((r) => <option key={r}>{r}</option>)}
                </select>
              </label>
              <label>Maturity level (SAMA only, 0–5)<input type="number" min={0} max={5} value={maturity} onChange={(e) => setMaturity(e.target.value)} /></label>
            </div>
            <fieldset>
              <legend>Accepted evidence linked to this requirement</legend>
              {accepted.length === 0 && <p className="muted">None. Add and accept evidence first.</p>}
              {accepted.map((e) => (
                <label className="check" key={e.id}>
                  <input type="checkbox" checked={chosen.includes(e.id)} onChange={(ev) => setChosen(ev.target.checked ? [...chosen, e.id] : chosen.filter((x) => x !== e.id))} />
                  {e.name}
                </label>))}
            </fieldset>
            <label>Notes <span className="muted">(required for not_applicable)</span><textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
            <Err msg={msg} />
            <div className="actions"><button className="approve">Save assessment</button></div>
          </>)}
        </form>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ mappings
const ORIGIN: Record<string, string> = { manual: "Manual", rule: "Similarity suggestion", llm: "AI-assisted", semantic: "Semantic" };

export function MappingsTab({ me }: { me: Me }) {
  const [status, setStatus] = useState("");
  const [data, err, reload] = useLoad(() => api.mappings(status), [status]);
  const [a, setA] = useState<Req | null>(null);
  const [b, setB] = useState<Req | null>(null);
  const [rel, setRel] = useState("partial_overlap");
  const [just, setJust] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const curator = ["reviewer", "platform_admin"].includes(me.role);

  async function create() {
    if (!a || !b) return;
    try {
      await api.mappingCreate({ source_req_id: a.id, target_req_id: b.id, relationship: rel, justification: just });
      setA(null); setB(null); setJust(""); setMsg(null); reload();
    } catch (x) { setMsg((x as Error).message); }
  }
  async function decide(id: number, decision: string) {
    let note: string | null = null;
    if (decision === "rejected") { note = window.prompt("Reason for rejecting (required):"); if (!note) return; }
    try { await api.mappingDecide(id, decision, note); setMsg(null); reload(); } catch (x) { setMsg((x as Error).message); }
  }

  return (
    <div>
      <h2 className="page-title">Cross-framework mappings</h2>
      <p className="muted">Mappings are written by a reviewer with a justification and approved by a different person. Similar wording is not treated as equivalence.</p>
      <label>Status
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All</option><option>pending</option><option>approved</option><option>rejected</option>
        </select>
      </label>
      <Err msg={err ?? msg} />
      <table>
        <thead><tr><th>From</th><th>Relationship</th><th>To</th><th>Justification</th><th>Origin</th><th>Status</th><th>Created / decided by</th><th /></tr></thead>
        <tbody>
          {data?.items.map((m) => (
            <tr key={m.id}>
              <td>{m.source_framework} {m.source_control_id}</td><td>{m.relationship}</td><td>{m.target_framework} {m.target_control_id}</td>
              <td>{m.justification}</td><td>{ORIGIN[m.method] ?? m.method}</td><td><span className={`badge ${m.approval_status}`}>{m.approval_status}</span></td>
              <td>{m.created_by}{m.decided_by ? ` / ${m.decided_by}` : ""}</td>
              <td className="actions">
                {curator && m.approval_status === "pending" && m.created_by !== me.email && (<>
                  <button className="approve" onClick={() => decide(m.id, "approved")}>Approve</button>
                  <button className="reject" onClick={() => decide(m.id, "rejected")}>Reject</button></>)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && data.items.length === 0 && <p className="empty">No mappings yet.</p>}
      {curator && <Suggestions me={me} onProposed={reload} />}
      {curator && (
        <form className="decide" onSubmit={(e) => { e.preventDefault(); create(); }}>
          <h3>Propose a mapping</h3>
          <ReqPicker label="From requirement" value={a} onChange={setA} />
          <ReqPicker label="To requirement" value={b} onChange={setB} />
          <label>Relationship
            <select value={rel} onChange={(e) => setRel(e.target.value)}>
              {["equivalent", "partial_overlap", "related", "independent"].map((r) => <option key={r}>{r}</option>)}
            </select>
          </label>
          <label>Justification <span className="muted">(at least 20 characters)</span><textarea rows={3} value={just} onChange={(e) => setJust(e.target.value)} /></label>
          <div className="actions"><button className="approve" disabled={!a || !b}>Propose</button></div>
        </form>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ changes
export function ChangesTab() {
  const [stats] = useLoad(() => api.stats(), []);
  const [fw, setFw] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [res, setRes] = useState<Row | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const frameworks = Array.from(new Set((stats ?? []).map((s) => s.framework)));
  const versions = Array.from(new Set((stats ?? []).filter((s) => s.framework === fw).map((s) => s.version)));
  const run = useCallback(() => api.changes(fw, from, to).then((r) => { setRes(r); setMsg(null); }).catch((e: Error) => { setRes(null); setMsg(e.message); }), [fw, from, to]);
  return (
    <div>
      <h2 className="page-title">Regulatory changes</h2>
      <p className="muted">Compares two loaded versions by control ID. A renumbered control appears as one removed and one added, so read the lists.</p>
      <div className="filters">
        <label>Framework<select value={fw} onChange={(e) => { setFw(e.target.value); setFrom(""); setTo(""); }}><option value="" />{frameworks.map((f) => <option key={f}>{f}</option>)}</select></label>
        <label>From<select value={from} onChange={(e) => setFrom(e.target.value)}><option value="" />{versions.map((v) => <option key={v}>{v}</option>)}</select></label>
        <label>To<select value={to} onChange={(e) => setTo(e.target.value)}><option value="" />{versions.map((v) => <option key={v}>{v}</option>)}</select></label>
        <button disabled={!fw || !from || !to} onClick={run}>Compare</button>
      </div>
      {fw && versions.length < 2 && <p className="muted">Only one version of {fw} is loaded, so there is nothing to compare yet.</p>}
      <Err msg={msg} />
      {res && (
        <>
          <p>Added {res.added.length} · Removed {res.removed.length} · Modified {res.modified.length} · Unchanged {res.unchanged}</p>
          {res.impact && <p className="note">Your organization: {res.impact.assessments_to_redo.length} assessment(s) to redo, {res.impact.evidence_to_revalidate.length} evidence item(s) to revalidate, {res.impact.new_requirements_to_assess.length} new requirement(s).</p>}
          {["added", "removed"].map((k) => res[k].length > 0 && <details key={k}><summary>{k} ({res[k].length})</summary>{res[k].map((x: Row) => <p key={x.control_id}><strong>{x.control_id}</strong> {x.text}</p>)}</details>)}
          {res.modified.length > 0 && <details open><summary>modified ({res.modified.length})</summary>{res.modified.map((x: Row) => <div key={x.control_id}><strong>{x.control_id}</strong><p className="muted">Before: {x.from_text}</p><p>After: {x.to_text}</p></div>)}</details>}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ maturity model
export function MaturityTab({ me }: { me: Me }) {
  const [data, err, reload] = useLoad(() => api.maturity(), []);
  const [msg, setMsg] = useState<string | null>(null);
  const curator = ["reviewer", "platform_admin"].includes(me.role);
  async function decide(kind: "levels" | "sections", id: number, decision: string) {
    let note: string | null = null;
    if (decision === "rejected") { note = window.prompt("Reason for rejecting (required):"); if (!note) return; }
    try { await api.maturityReview(kind, id, decision, note); setMsg(null); reload(); } catch (x) { setMsg((x as Error).message); }
  }
  const Controls = ({ kind, row }: { kind: "levels" | "sections"; row: Row }) => (
    <span className="actions">
      <span className={`badge ${row.validation_status}`}>{row.validation_status}</span>
      {curator && <><button className="approve" onClick={() => decide(kind, row.id, "approved")}>Approve</button>
        <button className="reject" onClick={() => decide(kind, row.id, "rejected")}>Reject</button></>}
    </span>
  );
  return (
    <div>
      <h2 className="page-title">Maturity model (as printed in the source)</h2>
      <Err msg={err ?? msg} />
      {data?.levels.map((l: Row) => (
        <section key={l.id} className="card">
          <h3>{l.framework} {l.version} · Level {l.level}: {l.name} <Controls kind="levels" row={l} /></h3>
          <p className="locate">Verify in the PDF: physical page {l.page_index}{l.page_label ? ` (printed ${l.page_label})` : ""}</p>
          {l.parse_notes.map((n: string) => <p className="note" key={n}>Source note: {n}</p>)}
          <strong>Definition and criteria</strong><ul>{l.criteria.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>
          <strong>Explanation</strong><ul>{l.explanation.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>
        </section>
      ))}
      {data?.sections.map((s: Row) => (
        <section key={s.id} className="card">
          <h3>{s.framework} · {s.section_id} {s.title} <Controls kind="sections" row={s} /></h3>
          <p className="locate">Verify in the PDF: physical page {s.page_index}{s.page_label ? ` (printed ${s.page_label})` : ""}</p>
          <blockquote>{s.original_text}</blockquote>
        </section>
      ))}
      {data && data.levels.length === 0 && <p className="empty">No maturity model loaded.</p>}
    </div>
  );
}
