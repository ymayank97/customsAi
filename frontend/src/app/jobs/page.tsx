"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, FileSpreadsheet, RefreshCw } from "lucide-react";
import { api, type JobSummary } from "@/lib/api";
import { AuthGate } from "@/components/AuthGate";
import { timeAgo, cn } from "@/lib/utils";

export default function JobsPage() {
  return (
    <AuthGate>
      <JobsInner />
    </AuthGate>
  );
}

function JobsInner() {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    setRefreshing(true);
    try {
      const j = await api.listJobs();
      setJobs(j);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="container-narrow pt-10 pb-24">
      <div className="flex items-end justify-between mb-6">
        <div>
          <h1 className="font-serif text-[40px] leading-tight tracking-tight">Audits</h1>
          <p className="mt-2 text-sm text-ink-muted">
            Past and in-progress workbook audits.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            className="btn-secondary text-sm"
            disabled={refreshing}
          >
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin")} />
            Refresh
          </button>
          <Link href="/upload" className="btn-primary text-sm">
            New audit
          </Link>
        </div>
      </div>

      {jobs === null ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton h-14 rounded-xl" />
          ))}
        </div>
      ) : jobs.length === 0 ? (
        <div className="surface px-5 py-12 text-center">
          <FileSpreadsheet className="mx-auto h-7 w-7 text-ink-muted" />
          <p className="mt-3 text-sm font-medium">No audits yet.</p>
          <p className="text-xs text-ink-muted">Upload a workbook to start your first audit.</p>
          <Link href="/upload" className="btn-primary mt-4 text-sm">
            Upload now
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      ) : (
        <div className="space-y-2 animate-fade-in">
          {jobs.map((j) => (
            <JobRow key={j.id} job={j} />
          ))}
        </div>
      )}
    </div>
  );
}

function JobRow({ job }: { job: JobSummary }) {
  const target =
    job.status === "done"
      ? `/results/${job.id}`
      : job.status === "failed"
      ? `/upload?job=${job.id}`
      : `/upload?job=${job.id}`;
  return (
    <Link href={target} className="surface surface-hover flex items-center gap-3 px-4 py-3">
      <span className="grid h-9 w-9 place-items-center rounded-lg bg-page border border-line">
        <FileSpreadsheet className="h-4 w-4 text-ink-soft" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{job.filename}</div>
        <div className="text-xs text-ink-muted">
          {job.rows_total ? `${job.rows_processed}/${job.rows_total} rows · ` : ""}
          {timeAgo(job.started_at)}
        </div>
      </div>
      <StatusPill status={job.status} />
    </Link>
  );
}

function StatusPill({ status }: { status: JobSummary["status"] }) {
  const map: Record<JobSummary["status"], { label: string; cls: string }> = {
    queued: { label: "Queued", cls: "bg-page text-ink-muted" },
    running: { label: "Running", cls: "bg-accent-soft text-accent" },
    done: { label: "Done", cls: "bg-success/10 text-success" },
    failed: { label: "Failed", cls: "bg-danger/10 text-danger" },
  };
  const { label, cls } = map[status];
  return (
    <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${cls}`}>
      {label}
    </span>
  );
}
