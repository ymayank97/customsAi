"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, FileSpreadsheet, Plus, ShieldCheck } from "lucide-react";
import { api, type JobSummary } from "@/lib/api";
import { useAuth } from "@/providers/AppProviders";
import { AuthGate } from "@/components/AuthGate";
import { timeAgo } from "@/lib/utils";

export default function HomePage() {
  return (
    <AuthGate>
      <Home />
    </AuthGate>
  );
}

function Home() {
  const { status, user } = useAuth();
  const [recent, setRecent] = useState<JobSummary[] | null>(null);

  const canFetch = status === "signed-in" || status === "disabled";

  useEffect(() => {
    if (!canFetch) {
      setRecent(null);
      return;
    }
    let alive = true;
    api
      .listJobs()
      .then((jobs) => alive && setRecent(jobs.slice(0, 5)))
      .catch(() => alive && setRecent([]));
    return () => {
      alive = false;
    };
  }, [canFetch]);

  const firstName = (user?.name || "").split(" ")[0];

  return (
    <div className="container-narrow pt-16 pb-24">
      <section className="text-center animate-fade-in">
        <h1 className="font-serif text-[44px] sm:text-[60px] leading-[1.02] tracking-tight">
          {firstName ? `Welcome, ${firstName}.` : "Customs audit"}
        </h1>
        <p className="mx-auto mt-4 max-w-[460px] text-[17px] leading-relaxed text-ink-soft">
          Upload a Bill of Entry. We check every line against the official tariff
          and show you which ones need a second look.
        </p>

        <div className="mt-10 flex justify-center">
          <Link href="/upload" className="btn-primary-xl">
            <Plus className="h-5 w-5" />
            Start a new audit
          </Link>
        </div>
        <p className="mt-4 text-[13px] text-ink-muted">
          Works with .xlsx and .xls files up to 10&nbsp;MB.
        </p>
      </section>

      {/* Recent audits */}
      <section className="mt-16 animate-slide-up">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="text-[15px] font-semibold tracking-tight">Your recent audits</h2>
          {recent && recent.length > 0 ? (
            <Link href="/jobs" className="text-[13px] text-ink-muted hover:text-ink transition">
              See all →
            </Link>
          ) : null}
        </div>

        {recent === null ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton h-16 rounded-2xl" />
            ))}
          </div>
        ) : recent.length === 0 ? (
          <div className="surface px-6 py-12 text-center">
            <span className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-accent-soft text-accent">
              <FileSpreadsheet className="h-6 w-6" />
            </span>
            <p className="mt-4 text-[15px] font-medium">No audits yet</p>
            <p className="mt-1 text-[13px] text-ink-muted">
              Your first audit takes about a minute to set up.
            </p>
            <Link href="/upload" className="btn-primary mt-5 px-5 py-2.5">
              Start your first audit
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        ) : (
          <div className="space-y-2">
            {recent.map((j) => (
              <RecentRow key={j.id} job={j} />
            ))}
          </div>
        )}
      </section>

      <p className="mt-12 flex items-center justify-center gap-2 text-center text-[12px] text-ink-muted">
        <ShieldCheck className="h-3.5 w-3.5 text-success" />
        Every suggested code is checked against the official customs tariff.
      </p>
    </div>
  );
}

function RecentRow({ job }: { job: JobSummary }) {
  const isDone = job.status === "done";
  const target = isDone ? `/results/${job.id}` : `/upload?job=${job.id}`;
  return (
    <Link
      href={target}
      className="surface surface-hover flex items-center gap-3.5 px-4 py-3.5"
    >
      <span className="grid h-11 w-11 place-items-center rounded-xl bg-page border border-line">
        <FileSpreadsheet className="h-5 w-5 text-ink-soft" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[15px] font-medium">{job.filename}</div>
        <div className="text-[13px] text-ink-muted">
          {job.rows_total ? `${job.rows_processed}/${job.rows_total} rows · ` : ""}
          started {timeAgo(job.started_at)}
        </div>
      </div>
      <StatusPill status={job.status} />
    </Link>
  );
}

function StatusPill({ status }: { status: JobSummary["status"] }) {
  const map: Record<JobSummary["status"], { label: string; cls: string }> = {
    queued: { label: "Waiting", cls: "bg-page text-ink-muted" },
    running: { label: "In progress", cls: "bg-accent-soft text-accent" },
    done: { label: "Ready", cls: "bg-success/10 text-success" },
    failed: { label: "Failed", cls: "bg-danger/10 text-danger" },
  };
  const { label, cls } = map[status];
  return (
    <span className={`shrink-0 rounded-full px-3 py-1 text-[12px] font-medium ${cls}`}>
      {label}
    </span>
  );
}
