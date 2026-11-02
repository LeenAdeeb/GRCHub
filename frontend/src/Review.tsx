import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "./api";
import type { Filters, Page, Req, ReqDetail, Stat, Status, Subdomain } from "./types";

const PAGE_SIZE = 25;
const STATUS_LABEL: Record<Status, string> = {
  pending_review: "Pending",
  approved: "Approved",
  rejected: "Rejected",
};

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Progress({ stats, framework }: { stats: Stat[]; framework: string }) {
  const rows = stats.filter((s) => !framework || s.framework === framework);
  const sum = (st: Status) => rows.filter((r) => r.status === st).reduce((a, r) => a + r.n, 0);
  const approved = sum("approved");
  const rejected = sum("rejected");
  const pending = sum("pending_review");
  const total = approved + rejected + pending;
  const pct = (n: number) => (total ? (n / total) * 100 : 0);
  return (
    <div className="progress" aria-label="Review progress">
      <div className="bar" role="img" aria-label={`${approved} approved, ${rejected} rejected, ${pending} pending`}>
        <span className="seg approved" style={{ width: `${pct(approved)}%` }} />
        <span className="seg rejected" style={{ width: `${pct(rejected)}%` }} />
      </div>
      <p>
        <strong>{approved}</strong> approved · <strong>{rejected}</strong> rejected · <strong>{pending}</strong> pending
        · {total} total
      </p>
    </div>
  );
}

function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge ${status}`}>{STATUS_LABEL[status]}</span>;
}

export default function Review({ canReview }: { canReview: boolean }) {
  const [filters, setFilters] = useState<Filters>({
    framework: "",
    status: "pending_review",
    subdomain: "",
    flagged: false,
    q: "",
  });
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page | null>(null);
  const [stats, setStats] = useState<Stat[]>([]);
  const [subdomains, setSubdomains] = useState<Subdomain[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ReqDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [note, setNote] = useState("");
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const debouncedQ = useDebounced(filters.q, 300);
  const activeFilters = useMemo(() => ({ ...filters, q: debouncedQ }), [filters, debouncedQ]);

  const frameworks = useMemo(() => Array.from(new Set(stats.map((s) => s.framework))), [stats]);

  const loadStats = useCallback(() => api.stats().then(setStats).catch((e: Error) => setError(e.message)), []);
  const loadList = useCallback(
    () =>
      api
        .list(activeFilters, offset, PAGE_SIZE)
        .then((p) => {
          setPage(p);
          setError(null);
        })
        .catch((e: Error) => setError(e.message)),
    [activeFilters, offset],
  );

  useEffect(() => void loadStats(), [loadStats]);
  useEffect(() => void loadList(), [loadList]);
  useEffect(() => {
    api.subdomains(filters.framework).then(setSubdomains).catch(() => setSubdomains([]));
  }, [filters.framework]);

  useEffect(() => {
    setNote("");
    setVerified(false);
    setFormError(null);
    if (selectedId == null) {
      setDetail(null);
      return;
    }
    api.get(selectedId).then(setDetail).catch((e: Error) => setError(e.message));
  }, [selectedId]);

  function update(patch: Partial<Filters>) {
    setFilters((f) => ({ ...f, ...patch }));
    setOffset(0);
  }

  async function decide(decision: "approved" | "rejected") {
    if (!detail) return;
    if (decision === "rejected" && !note.trim()) {
      setFormError("A note is required when rejecting.");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const idx = page?.items.findIndex((r) => r.id === detail.id) ?? -1;
      await api.review(detail.id, { decision, note: note.trim() || null });
      const fresh = await api.list(activeFilters, offset, PAGE_SIZE);
      setPage(fresh);
      loadStats();
      // Move on to the next item in the list (the decided one leaves the list when filtering by pending).
      const stillThere = fresh.items.findIndex((r) => r.id === detail.id);
      if (stillThere >= 0) {
        setSelectedId(fresh.items[Math.min(stillThere + 1, fresh.items.length - 1)]?.id ?? null);
        setDetail(await api.get(detail.id));
      } else {
        setSelectedId(fresh.items[Math.min(Math.max(idx, 0), fresh.items.length - 1)]?.id ?? null);
      }
      setNote("");
      setVerified(false);
    } catch (e) {
      const err = e as ApiError;
      setFormError(err.status === 403 ? "Your role cannot review requirements." : err.message);
    } finally {
      setBusy(false);
    }
  }

  const canApprove = canReview && verified && !busy;
  const canReject = canReview && !busy;
  const totalPages = page ? Math.max(1, Math.ceil(page.total / PAGE_SIZE)) : 1;

  return (
    <div>
      <header>
        <h2 className="page-title">Requirement review</h2>
        <Progress stats={stats} framework={filters.framework} />
      </header>

      <section className="filters" aria-label="Filters">
        <label>
          Framework
          <select value={filters.framework} onChange={(e) => update({ framework: e.target.value, subdomain: "" })}>
            <option value="">All</option>
            {frameworks.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select value={filters.status} onChange={(e) => update({ status: e.target.value as Status | "" })}>
            <option value="pending_review">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="">All</option>
          </select>
        </label>
        <label>
          Subdomain
          <select value={filters.subdomain} onChange={(e) => update({ subdomain: e.target.value })}>
            <option value="">All</option>
            {subdomains.map((s) => (
              <option key={`${s.framework}-${s.subdomain_id}`} value={s.subdomain_id}>
                {s.subdomain_id} {s.subdomain_title.slice(0, 40)}
              </option>
            ))}
          </select>
        </label>
        <label className="grow">
          Search text
          <input type="search" value={filters.q} onChange={(e) => update({ q: e.target.value })} placeholder="e.g. approved" />
        </label>
        <label className="check">
          <input type="checkbox" checked={filters.flagged} onChange={(e) => update({ flagged: e.target.checked })} />
          Flagged only
        </label>
      </section>

      {error && (
        <p className="error" role="alert">
          Could not load data: {error}
        </p>
      )}

      <main>
        <nav className="list" aria-label="Requirements">
          {page && page.items.length === 0 && (
            <p className="empty">
              {page.total === 0 && !filters.q && !filters.subdomain && !filters.flagged && stats.length === 0
                ? "No requirements in the database yet. Run db-load for a registered source."
                : "No requirements match these filters."}
            </p>
          )}
          <ul>
            {page?.items.map((r: Req) => (
              <li key={r.id}>
                <button className={r.id === selectedId ? "row selected" : "row"} onClick={() => setSelectedId(r.id)}>
                  <span className="rid">{r.control_id}</span>
                  <span className="rtext">{r.original_text}</span>
                  <span className="rmeta">
                    <StatusBadge status={r.validation_status} />
                    {(r.extraction_confidence < 0.85 || r.parse_notes.length > 0) && <span className="flag">flagged</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="pager">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </button>
            <span>
              Page {Math.floor(offset / PAGE_SIZE) + 1} of {totalPages} · {page?.total ?? 0} items
            </span>
            <button disabled={!page || offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
              Next
            </button>
          </div>
        </nav>

        <article className="detail" aria-live="polite">
          {!detail ? (
            <p className="empty">Select a requirement to review it.</p>
          ) : (
            <>
              <h2>
                {detail.framework} {detail.version} · {detail.control_id} <StatusBadge status={detail.validation_status} />
              </h2>
              <p className="crumbs">
                {detail.domain} › {detail.subdomain_id} {detail.subdomain_title}
              </p>
              <p className="locate">
                Verify in the official PDF: <strong>physical page {detail.page_index}</strong>
                {detail.page_label ? ` (printed page ${detail.page_label})` : ""}
              </p>
              {detail.id_is_derived && (
                <p className="note">
                  This ID is derived by GRCHub: the source prints only “{detail.printed_id}” for this item.
                </p>
              )}
              {detail.parse_notes.map((n) => (
                <p className="note" key={n}>
                  Source note: {n}
                </p>
              ))}
              <blockquote>{detail.original_text}</blockquote>
              <p className="muted">
                Extraction confidence {detail.extraction_confidence.toFixed(2)} · kind {detail.kind}
              </p>
              {(detail.principle || detail.objective) && (
                <details>
                  <summary>Context from the source</summary>
                  {detail.principle && (
                    <p>
                      <strong>Principle:</strong> {detail.principle}
                    </p>
                  )}
                  {detail.objective && (
                    <p>
                      <strong>Objective:</strong> {detail.objective}
                    </p>
                  )}
                </details>
              )}

              <form
                className="decide"
                onSubmit={(e) => {
                  e.preventDefault();
                }}
              >
                <h3>Your decision</h3>
                {!canReview && <p className="muted">Only reviewers and platform admins can decide. The decision is recorded under your login.</p>}
                <label>
                  Note {` `}
                  <span className="muted">(required to reject)</span>
                  <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} />
                </label>
                <label className="check">
                  <input type="checkbox" checked={verified} onChange={(e) => setVerified(e.target.checked)} />
                  I compared this text with the cited page of the official PDF and it matches exactly.
                </label>
                {formError && (
                  <p className="error" role="alert">
                    {formError}
                  </p>
                )}
                <div className="actions">
                  <button type="button" className="approve" disabled={!canApprove} onClick={() => decide("approved")}>
                    Approve
                  </button>
                  <button type="button" className="reject" disabled={!canReject} onClick={() => decide("rejected")}>
                    Reject
                  </button>
                </div>
              </form>

              {detail.reviews.length > 0 && (
                <section>
                  <h3>Review history</h3>
                  <ul className="history">
                    {detail.reviews.map((h, i) => (
                      <li key={i}>
                        <strong>{h.decision}</strong> by {h.reviewer} · {new Date(h.decided_at).toLocaleString()}
                        {h.note ? ` — ${h.note}` : ""}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
        </article>
      </main>
    </div>
  );
}
