"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Download, Search, MessageSquare, ClipboardCheck } from "lucide-react";
import { api, type RowResult, type TrainingLabel } from "@/lib/api";
import { useAuth } from "@/providers/AppProviders";
import { AuthGate } from "@/components/AuthGate";
import { formatINR, timeAgo } from "@/lib/utils";
import { StatCard } from "@/components/StatCard";
import { ResultsTable } from "@/components/ResultsTable";
import { ChatDrawer } from "@/components/ChatDrawer";
import { CorrectionDialog } from "@/components/CorrectionDialog";

export default function ResultsPage() {
  return <AuthGate><ResultsInner /></AuthGate>;
}

function ResultsInner() {
  const { jobId } = useParams<{ jobId: string }>();
  const { user } = useAuth();
  const [rows, setRows] = useState<RowResult[] | null>(null);
  const [filename, setFilename] = useState("");
  const [status, setStatus] = useState("");
  const [processed, setProcessed] = useState(0);
  const [totalRows, setTotalRows] = useState(0);
  const [completedAt, setCompletedAt] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const [attentionOnly, setAttentionOnly] = useState(false);
  const [reviewFilter, setReviewFilter] = useState("all");
  const [chatOpen, setChatOpen] = useState(false);
  const [chatRow, setChatRow] = useState<RowResult | null>(null);
  const [busyRows, setBusyRows] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [editRow, setEditRow] = useState<RowResult | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [downloadBusy, setDownloadBusy] = useState(false);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const job = await api.jobStatus(jobId);
        if (!alive) return;
        setFilename(job.filename); setStatus(job.status); setProcessed(job.rows_processed);
        setTotalRows(job.rows_total); setCompletedAt(job.completed_at || null);
        if (job.status === "done" || job.status === "failed") {
          const results = await api.results(jobId, { page_size: 100000 });
          if (alive) { setRows(results.rows); setError(job.status === "failed" ? "This audit stopped before finishing. The available results are shown below." : null); }
          return;
        }
        setError(null);
        timer = setTimeout(tick, 1500);
      } catch (issue) {
        if (alive) { setError(issue instanceof Error ? issue.message : "Could not load this audit."); timer = setTimeout(tick, 3000); }
      }
    }
    void tick();
    return () => { alive = false; clearTimeout(timer); };
  }, [jobId]);

  const stats = useMemo(() => ({
    total: rows?.length || 0,
    attention: rows?.filter(row => row.fraud.is_fraud || row.final.manual_queue || row.review_flags?.length || row.retrieval_diagnostics?.ambiguous).length || 0,
    risk: rows?.reduce((sum, row) => sum + (row.fraud.revenue_at_risk || 0), 0) || 0,
    reviewed: rows?.filter(row => row.review).length || 0,
  }), [rows]);
  const visibleRows = useMemo(() => (rows || []).filter(row => {
    if (attentionOnly && !(row.fraud.is_fraud || row.final.manual_queue || row.review_flags?.length || row.retrieval_diagnostics?.ambiguous)) return false;
    if (reviewFilter === "pending") return !row.review;
    if (reviewFilter === "reviewed") return row.review && row.review.decision !== "needs_information";
    if (reviewFilter === "details") return row.review?.decision === "needs_information";
    return true;
  }), [rows, attentionOnly, reviewFilter]);
  const onAskAi = useCallback((row: RowResult) => { setChatRow(row); setChatOpen(true); }, []);
  const onEdit = useCallback((row: RowResult) => setEditRow(row), []);
  const onSaved = useCallback((row: RowResult, review: TrainingLabel) => {
    setRows(previous => previous?.map(item => item.row_idx === row.row_idx ? { ...item, review } : item) || null);
  }, []);
  const onReevaluate = useCallback(async (row: RowResult) => {
    setBusyRows(previous => new Set(previous).add(row.row_idx));
    try {
      const result = await api.reevaluate({ item_description: row.item_description, declared_cth: row.declared_cth, declared_bcd: row.declared_bcd, top_k: 8 });
      setRows(previous => previous?.map(item => item.row_idx === row.row_idx ? { ...item, final: { ...item.final, candidates: result.candidates } } : item) || null);
      setToast("Additional tariff options are available in the item details.");
    } catch (issue) { setToast(issue instanceof Error ? issue.message : "Could not check this item again."); }
    finally { setBusyRows(previous => { const next = new Set(previous); next.delete(row.row_idx); return next; }); }
  }, []);

  return <div className="container-wide pt-8 pb-20">
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <Link href="/jobs" className="inline-flex items-center gap-1 text-sm text-ink-muted hover:text-ink"><ArrowLeft className="h-4 w-4" /> Back to audits</Link>
        <h1 className="mt-3 break-words font-serif text-3xl sm:text-4xl">{filename || "Audit results"}</h1>
        <p className="mt-2 text-sm text-ink-muted">{completedAt ? "Completed " + timeAgo(completedAt) :
          status ? processed.toLocaleString() + " of " + totalRows.toLocaleString() + " items checked" : "Loading audit…"}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn-secondary" onClick={() => { setChatRow(null); setChatOpen(true); }}><MessageSquare className="h-4 w-4" /> Explain results</button>
        <button className="btn-primary" disabled={status !== "done" || downloadBusy} onClick={async () => {
          setDownloadBusy(true);
          try { await api.downloadAnnotated(jobId, filename); }
          catch (issue) { setToast(issue instanceof Error ? issue.message : "Download failed."); }
          finally { setDownloadBusy(false); }
        }}><Download className="h-4 w-4" />{downloadBusy ? "Downloading…" : "Download audit report"}</button>
      </div>
    </div>
    {rows && <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard label="Items checked" value={stats.total.toLocaleString()} />
      <StatCard label="Need attention" value={stats.attention.toLocaleString()} hint="Review the product facts and tariff" tone={stats.attention ? "warn" : "neutral"} />
      <StatCard label="Potential duty difference" value={formatINR(stats.risk)} hint="Based on available duty data" />
      <StatCard label="Officer reviews saved" value={stats.reviewed.toLocaleString() + " / " + stats.total.toLocaleString()} hint="Includes requests for more details" tone="success" />
    </div>}
    {user?.can_label_rows ? <div className="mb-6 flex flex-wrap items-start justify-between gap-4 rounded-xl border border-accent/20 bg-accent-soft/30 p-5">
      <div className="flex gap-3"><ClipboardCheck className="mt-0.5 h-5 w-5 shrink-0 text-accent" /><div>
        <h2 className="font-semibold">Check an item, then save your decision</h2>
        <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-soft">Choose Review item to confirm a code, enter a correction, or request more details. Add the document or tariff reference you checked. Your saved review stays beside the original suggestion.</p>
      </div></div><Link href="/reviewed-items" className="btn-secondary">View reviewed items</Link>
    </div> : <p className="mb-6 rounded-xl border border-line bg-surface p-4 text-sm text-ink-soft">You can view and download this audit. Approved premium reviewers can also record verified decisions for future model training.</p>}
    <div className="mb-4 flex flex-wrap gap-3">
      <div className="relative min-w-0 flex-1 basis-64">
        <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-ink-muted" />
        <input aria-label="Search items" value={filter} onChange={event => setFilter(event.target.value)} placeholder="Search description or tariff code" className="input min-h-10 pl-10" />
      </div>
      <button aria-pressed={attentionOnly} onClick={() => setAttentionOnly(value => !value)} className={attentionOnly ? "btn-primary" : "btn-secondary"}>Needs attention</button>
      <select aria-label="Filter officer reviews" className="input w-auto" value={reviewFilter} onChange={event => setReviewFilter(event.target.value)}>
        <option value="all">All items</option><option value="pending">Awaiting officer review</option><option value="reviewed">Verified reviews</option><option value="details">More details needed</option>
      </select>
    </div>
    {error && <p role="alert" className="mb-4 rounded-xl border border-danger/20 bg-danger/5 p-4 text-sm text-danger">{error}</p>}
    {!rows ? <div role="status" aria-label="Loading items" className="space-y-3">{[0, 1, 2].map(index => <div key={index} className="skeleton h-16 rounded-xl" />)}</div> :
      <ResultsTable rows={visibleRows} filter={filter} fraudOnly={false} onAskAi={onAskAi} onReevaluate={onReevaluate} onEdit={user?.can_label_rows && status === "done" ? onEdit : undefined} busyRows={busyRows} />}
    <ChatDrawer open={chatOpen} onClose={() => setChatOpen(false)} contextRow={chatRow} />
    <CorrectionDialog row={editRow} jobId={jobId} onClose={() => setEditRow(null)} onSaved={onSaved} />
    {toast && <div role="status" className="fixed bottom-6 left-1/2 z-[70] w-[min(90vw,480px)] -translate-x-1/2 rounded-xl border border-line bg-surface p-4 text-sm shadow-lift">{toast}</div>}
  </div>;
}
