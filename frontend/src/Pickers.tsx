import { useEffect, useState } from "react";
import { api } from "./api";
import type { Req } from "./types";

/** Search approved requirements by text and pick one. Only approved requirements can be linked or assessed. */
export function ReqPicker({ label, value, onChange }: { label: string; value: Req | null; onChange: (r: Req | null) => void }) {
  const [q, setQ] = useState("");
  const [framework, setFramework] = useState("");
  const [frameworks, setFrameworks] = useState<string[]>([]);
  const [items, setItems] = useState<Req[]>([]);
  useEffect(() => {
    api.stats().then((s) => setFrameworks(Array.from(new Set(s.map((x) => x.framework)))));
  }, []);
  useEffect(() => {
    if (!q.trim() && !framework) return setItems([]);
    const t = setTimeout(() => {
      api
        .list({ framework, status: "approved", subdomain: "", flagged: false, q }, 0, 8)
        .then((p) => setItems(p.items))
        .catch(() => setItems([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, framework]);
  if (value)
    return (
      <div className="picked">
        <strong>{label}:</strong> {value.framework} {value.control_id} — {value.original_text.slice(0, 90)}…{" "}
        <button type="button" onClick={() => onChange(null)}>
          Change
        </button>
      </div>
    );
  return (
    <div className="picker">
      <strong>{label}</strong>
      <div className="two">
        <select value={framework} onChange={(e) => setFramework(e.target.value)} aria-label="Framework">
          <option value="">Any framework</option>
          {frameworks.map((f) => (
            <option key={f}>{f}</option>
          ))}
        </select>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search approved requirement text" />
      </div>
      <ul className="choices">
        {items.map((r) => (
          <li key={r.id}>
            <button type="button" className="row" onClick={() => onChange(r)}>
              <span className="rid">{r.control_id}</span>
              <span className="rtext">{r.original_text}</span>
            </button>
          </li>
        ))}
      </ul>
      {(q || framework) && items.length === 0 && <p className="muted">No approved requirement matches. Requirements must be approved by a reviewer first.</p>}
    </div>
  );
}
