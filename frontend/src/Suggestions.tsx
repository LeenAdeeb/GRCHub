import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Me, Row } from "./api";
import { useLoad } from "./Org";

const PAGE = 10;
const REL_LABEL: Record<string, string> = {
  equivalent: "Equivalent", partial_overlap: "Partial overlap", related: "Related", independent: "Independent",
};
const Err = ({ msg }: { msg: string | null }) => (msg ? <p className="error" role="alert">{msg}</p> : null);

function Quote({ label, text, quote }: { label: string; text: string; quote: string | null }) {
  if (!quote || !text.toLowerCase().includes(quote.toLowerCase().slice(0, 12))) return <p className="rtext">{text}</p>;
  const i = text.toLowerCase().indexOf(quote.toLowerCase());
  if (i < 0) return <p className="rtext">{text}</p>;
  return (
    <p className="rtext" aria-label={label}>
      {text.slice(0, i)}<mark>{text.slice(i, i + quote.length)}</mark>{text.slice(i + quote.length)}
    </p>
  );
}

function Card({ s, onDone }: { s: Row; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [rel, setRel] = useState<string>(s.llm_relationship && s.llm_relationship !== "independent" ? s.llm_relationship : "related");
  const [just, setJust] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const blocked = s.stale || s.source_status !== "approved" || s.target_status !== "approved";

  async function propose() {
    try { await api.sugPropose(s.id, rel, just); onDone(); } catch (x) { setMsg((x as Error).message); }
  }
  async function dismiss() {
    try { await api.sugDismiss(s.id, null); onDone(); } catch (x) { setMsg((x as Error).message); }
  }
  return (
    <section className="card">
      <div className="two">
        <div>
          <strong>{s.source_framework} {s.source_control_id}</strong>
          <Quote label="source text" text={s.source_text} quote={s.llm_source_quote} />
        </div>
        <div>
          <strong>{s.target_framework} {s.target_control_id}</strong>
          <Quote label="target text" text={s.target_text} quote={s.llm_target_quote} />
        </div>
      </div>
      <p className="muted">
        Text similarity {(s.lexical_score * 100).toFixed(0)}%
        {s.llm_relationship && (
          <> · AI: <strong>{REL_LABEL[s.llm_relationship]}</strong>{" "}
            {s.llm_verified ? <span className="badge approved">quotes verified</span> : <span className="flag">quotes not found in the text: do not rely on this</span>}</>
        )}
        {!s.llm_relationship && " · not checked by AI"}
      </p>
      {s.llm_rationale && <p className="note">AI rationale (a draft, not a finding): {s.llm_rationale}</p>}
      {s.stale && <p className="flag">A requirement's text changed since this was scored. Generate suggestions again.</p>}
      {(s.source_status !== "approved" || s.target_status !== "approved") && !s.stale && (
        <p className="flag">One of the requirements is not approved yet; approve it before proposing a mapping.</p>
      )}
      <Err msg={msg} />
      {s.status === "suggested" && !open && (
        <div className="actions">
          <button className="approve" disabled={blocked} onClick={() => setOpen(true)}>Propose mapping…</button>
          <button onClick={dismiss}>Dismiss</button>
        </div>
      )}
      {open && (
        <div className="decide">
          <label>Relationship
            <select value={rel} onChange={(e) => setRel(e.target.value)}>
              {Object.entries(REL_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label>Your justification <span className="muted">(your words, 20+ characters; another reviewer must approve it)</span>
            <textarea rows={3} value={just} onChange={(e) => setJust(e.target.value)} />
          </label>
          {s.llm_rationale && <button type="button" onClick={() => setJust(`Draft from AI, to be checked against both texts: ${s.llm_rationale}`)}>Start from the AI draft</button>}
          <div className="actions">
            <button className="approve" onClick={propose}>Submit for approval</button>
            <button onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </div>
      )}
      {s.status === "proposed" && <p className="muted">Proposed as mapping #{s.mapping_id}.</p>}
    </section>
  );
}

export default function Suggestions({ me, onProposed }: { me: Me; onProposed: () => void }) {
  const [stats] = useLoad(() => api.stats(), []);
  const [cfg] = useLoad(() => api.sugConfig(), []);
  const frameworks = Array.from(new Set((stats ?? []).map((s) => s.framework)));
  const [src, setSrc] = useState("");
  const [tgt, setTgt] = useState("");
  const [status, setStatus] = useState("suggested");
  const [rel, setRel] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, err, reload] = useLoad(
    () => api.sugList({ status, limit: String(PAGE), offset: String(offset), ...(src && { source_framework: src }), ...(tgt && { target_framework: tgt }), ...(rel && { relationship: rel }) }),
    [status, src, tgt, rel, offset],
  );
  const [msg, setMsg] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [est, setEst] = useState<Row | null>(null);
  const [maxCalls, setMaxCalls] = useState(50);
  const [provider, setProvider] = useState("");
  const [job, setJob] = useState<Row | null>(null);
  const timer = useRef<number | null>(null);
  const isAdmin = me.role === "platform_admin";
  const ready = src !== "" && tgt !== "" && src !== tgt;
  const providers: Row[] = cfg?.providers ?? [];
  const chosen = provider || cfg?.default_provider || "";
  const chosenInfo = providers.find((p) => p.provider === chosen);

  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  async function generate() {
    setMsg(null); setInfo(null);
    try {
      const r = await api.sugGenerate({ source_framework: src, target_framework: tgt });
      setInfo(`${r.created} new candidate pair(s) from ${r.source_requirements} × ${r.target_requirements} approved requirements (${r.skipped_existing} already existed or were already mapped). Only approved requirements are compared.`);
      setOffset(0); reload();
    } catch (x) { setMsg((x as Error).message); }
  }
  async function estimate() {
    setMsg(null);
    try { setEst(await api.sugEstimate(src, tgt, chosen)); } catch (x) { setMsg((x as Error).message); }
  }
  async function run() {
    setMsg(null);
    try {
      const { job_id } = await api.sugRun({ source_framework: src, target_framework: tgt, max_calls: maxCalls, provider: chosen || undefined });
      setJob({ state: "running", done: 0, total: 0 });
      timer.current = window.setInterval(async () => {
        try {
          const j = await api.sugJob(job_id);
          setJob(j);
          if (j.state !== "running") { if (timer.current) window.clearInterval(timer.current); reload(); setEst(null); }
        } catch { if (timer.current) window.clearInterval(timer.current); }
      }, 2000);
    } catch (x) { setMsg((x as Error).message); }
  }
  const done = () => { reload(); onProposed(); };

  return (
    <section>
      <h3>Suggested mappings</h3>
      <p className="muted">
        Step 1 is free and runs on this computer: it ranks the most similar requirements between two frameworks.
        Step 2 (optional) asks Claude to judge only those few pairs, one request per requirement, and must quote both texts;
        quotes that are not in the real text are flagged. Suggestions are candidates only: you propose a mapping in your own
        words and a second reviewer approves it.
      </p>
      <div className="filters">
        <label>From<select value={src} onChange={(e) => { setSrc(e.target.value); setOffset(0); }}><option value="" />{frameworks.map((f) => <option key={f}>{f}</option>)}</select></label>
        <label>To<select value={tgt} onChange={(e) => { setTgt(e.target.value); setOffset(0); }}><option value="" />{frameworks.map((f) => <option key={f}>{f}</option>)}</select></label>
        <button disabled={!ready} onClick={generate}>1. Find candidates</button>
        {isAdmin && providers.length > 0 && (
          <label>AI provider
            <select value={chosen} onChange={(e) => { setProvider(e.target.value); setEst(null); }}>
              {providers.map((p) => <option key={p.provider} value={p.provider}>{p.provider}{p.model ? ` (${p.model})` : " (model not set)"}</option>)}
            </select>
          </label>
        )}
        {isAdmin && <button disabled={!ready || !chosenInfo?.ready} onClick={estimate} title={cfg?.llm_configured ? "" : "No AI key is set on the server"}>2. Estimate AI cost</button>}
      </div>
      {isAdmin && cfg && !cfg.llm_configured && <p className="muted">AI refinement is off: add an AI key (Anthropic, OpenAI or Gemini) to the server settings, see the README. Step 1 works without it.</p>}
      {isAdmin && chosenInfo && !chosenInfo.ready && <p className="flag">Set GRCHUB_{chosen.toUpperCase()}_MODEL in .env to name the {chosen} model to use, then restart.</p>}
      {!isAdmin && <p className="muted">AI refinement can be started by a platform admin.</p>}
      <Err msg={msg ?? err} />
      {info && <p className="note" role="status">{info}</p>}
      {est && (
        <div className="card">
          <p>
            {est.calls} request(s) covering {est.pairs} pair(s), about {est.approx_input_tokens.toLocaleString()} input tokens, model <code>{est.model}</code>.
            {est.calls === 0 && " Nothing left to refine."}
          </p>
          {est.calls > 0 && (
            <div className="inline">
              <label>Max requests this run <input type="number" min={1} max={500} value={maxCalls} onChange={(e) => setMaxCalls(Number(e.target.value))} /></label>
              <button className="approve" disabled={job?.state === "running"} onClick={run}>Run AI refinement</button>
            </div>
          )}
        </div>
      )}
      {job && (
        <p className={job.state === "failed" ? "error" : "note"} role="status">
          {job.state === "running" && `Working… ${job.done}/${job.total || "?"}`}
          {job.state === "finished" && `Finished: ${job.result.refined} pair(s) judged, ${job.result.unverified} with unverifiable quotes, ${job.result.failed} request(s) failed, ${job.result.remaining_after_budget} left for another run.`}
          {job.state === "failed" && `Stopped: ${job.error}`}
        </p>
      )}
      <div className="filters">
        <label>Show<select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}><option value="suggested">To review</option><option value="proposed">Proposed</option><option value="dismissed">Dismissed</option></select></label>
        <label>AI result<select value={rel} onChange={(e) => { setRel(e.target.value); setOffset(0); }}><option value="">Any (independent hidden)</option>{Object.entries(REL_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
      </div>
      {data?.items.map((s) => <Card key={s.id} s={s} onDone={done} />)}
      {data && data.items.length === 0 && <p className="empty">No suggestions here. Approve requirements in both frameworks, pick them above and press “Find candidates”.</p>}
      {data && data.total > PAGE && (
        <div className="pager">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
          <span>{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</span>
          <button disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
        </div>
      )}
    </section>
  );
}
