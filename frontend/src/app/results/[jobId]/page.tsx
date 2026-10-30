"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, type JobDetail, type ResultsPage as ResultData, type RowResult } from "@/lib/api";
import { AuthGate } from "@/components/AuthGate";
import { ResultsTable } from "@/components/ResultsTable";
import { CorrectionDialog } from "@/components/CorrectionDialog";
import { StatCard } from "@/components/StatCard";
import { formatINR } from "@/lib/utils";

export default function ResultsPage() {
  return <AuthGate><Results /></AuthGate>;
}

function Results() {
  const router = useRouter();
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [data, setData] = useState<ResultData | null>(null);
  const [page, setPage] = useState(1);
  const [findingsOnly, setFindingsOnly] = useState(false);
  const [editRow, setEditRow] = useState<RowResult | null>(null);
  const [error, setError] = useState("");
  const [reclassifying, setReclassifying] = useState(false);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    setData(null);
    const load = async () => {
      try {
        const next = await api.jobStatus(jobId);
        if (!alive) return;
        setJob(next);
        if (next.status === "done") {
          const result = await api.results(jobId, { page, page_size: 50, fraud_only: findingsOnly });
          if (alive) { setData(result); setError(""); }
        } else if (next.status === "failed") setError(next.error || "Processing failed");
        else timer = setTimeout(load, 1500);
      } catch (e) { if (alive) setError(e instanceof Error ? e.message : String(e)); }
    };
    void load();
    return () => { alive = false; clearTimeout(timer); };
  }, [jobId, page, findingsOnly]);
  const summary = data?.summary;
  return <div className="container-wide pt-8 pb-24">
    <Link href="/jobs" className="text-sm text-ink-muted">← All audits</Link>
    <div className="flex items-center justify-between gap-4 my-5">
      <h1 className="font-serif text-3xl">{job?.filename || "Audit results"}</h1>
      <button disabled={job?.status !== "done" || reclassifying} className="btn-secondary disabled:opacity-50" onClick={async () => {
        setReclassifying(true);
        try { const run = await api.reclassify(jobId); router.push(`/results/${run.job_id}`); }
        catch (e) { setError(e instanceof Error ? e.message : String(e)); }
        finally { setReclassifying(false); }
      }}>{reclassifying ? "Starting…" : "Reclassify"}</button>
      <button disabled={job?.status !== "done"} className="btn-primary disabled:opacity-50" onClick={async () => {
        try { await api.downloadAnnotated(jobId, job?.filename); }
        catch (e) { setError(e instanceof Error ? e.message : String(e)); }
      }}>Download Excel</button>
    </div>
    <p className="mb-5 text-sm text-ink-muted">Statistical suggestions require tariff and legal verification. Uncertain classifications remain open for review.</p>
    <p className="mb-5 text-xs text-ink-muted">BCD scores measure rate agreement out of 100. Estimated CTHs and statutory rates are shown when verification is pending; concessions may change the applicable rate. Red rows need review, including estimated matches of 100/100. Fraud review priority is not proof of fraud.</p>
    {summary && <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
      <StatCard label="Rows" value={String(summary.total)} hint={`${summary.review} require classification review`} />
      <StatCard label="Suggestions" value={`${summary.suggested ?? summary.classified}/${summary.total}`} hint={`${summary.classified} automatically accepted`} />
      <StatCard label="Potential discrepancies" value={String(summary.findings)} />
      <StatCard label="Potential BCD difference" value={formatINR(summary.revenue_at_risk)} />
    </div>}
    <label className="block mb-3 text-sm"><input type="checkbox" checked={findingsOnly} onChange={e => {
      setFindingsOnly(e.target.checked); setPage(1);
    }} /> Show potential underpayments only</label>
    {error && <p role="alert" className="mb-4 text-danger">{error}</p>}
    {data ? <><ResultsTable rows={data.rows} onEdit={setEditRow} />
      <div className="flex items-center justify-end gap-4 mt-4 text-sm">
        <button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</button>
        <span>Page {page} of {Math.max(1, Math.ceil(data.total / 50))}</span>
        <button className="btn-secondary" disabled={page * 50 >= data.total} onClick={() => setPage(p => p + 1)}>Next</button>
      </div></> : <p>{job?.status === "failed" ? "Audit failed" : `Processing ${job?.rows_processed ?? 0} of ${job?.rows_total ?? "…"} rows`}</p>}
    <CorrectionDialog row={editRow} jobId={jobId} onClose={() => setEditRow(null)}
      onSaved={() => { setEditRow(null); }} />
  </div>;
}
