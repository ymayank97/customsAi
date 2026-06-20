"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Download, Search, MessageSquare, Filter, AlertTriangle } from "lucide-react";
import { api, type RowResult } from "@/lib/api";
import { AuthGate } from "@/components/AuthGate";
import { cn, formatINR, timeAgo } from "@/lib/utils";
import { StatCard } from "@/components/StatCard";
import { ResultsTable } from "@/components/ResultsTable";
import { ChatDrawer } from "@/components/ChatDrawer";
import { CorrectionDialog } from "@/components/CorrectionDialog";

export default function ResultsPage() {
  return (
    <AuthGate>
      <ResultsInner />
    </AuthGate>
  );
}

function ResultsInner() {
  const { jobId } = useParams<{ jobId: string }>();

  const [rows, setRows] = useState<RowResult[] | null>(null);
  const [filename, setFilename] = useState<string>("");
  const [status, setStatus] = useState<string>("");
  const [processed, setProcessed] = useState(0);
  const [totalRows, setTotalRows] = useState(0);
  const [completedAt, setCompletedAt] = useState<number | null>(null);
  const [filter, setFilter] = useState("");
  const [fraudOnly, setFraudOnly] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatRow, setChatRow] = useState<RowResult | null>(null);
  const [busyRows, setBusyRows] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [editRow, setEditRow] = useState<RowResult | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const job = await api.jobStatus(jobId);
        if (!alive) return;
        setFilename(job.filename);
        setStatus(job.status);
        setProcessed(job.rows_processed ?? 0);
        setTotalRows(job.rows_total ?? 0);
        setCompletedAt(job.completed_at ?? null);

        if (job.status === "done" || job.status === "failed") {
          const results = await api.results(jobId, { page: 1, page_size: 100_000 });
          if (!alive) return;
          setRows(results.rows);
          setError(null);
          return;
        }

        setError(null);
        timer = setTimeout(tick, 1500);
      } catch (e: any) {
        if (!alive) return;
        setError(e?.message ?? "Could not load results");
        timer = setTimeout(tick, 3000);
      }
    };
    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [jobId]);

  const stats = useMemo(() => {
    if (!rows) return null;
    const fraud = rows.filter((r) => r.fraud?.is_fraud);
    const cthMismatch = fraud.filter((r) => (r.fraud.flag_type || "").includes("CTH")).length;
    const totalRisk = fraud.reduce((s, r) => s + (r.fraud.revenue_at_risk ?? 0), 0);
    const manualQueue = rows.filter(
      (r) => r.final?.manual_queue || (r.review_flags ?? []).includes("manual_review")
    ).length;
    return {
      total: rows.length,
      fraud: fraud.length,
      cthMismatch,
      totalRisk,
      manualQueue,
    };
  }, [rows]);

  const onAskAi = useCallback((row: RowResult) => {
    setChatRow(row);
    setChatOpen(true);
  }, []);

  const onEdit = useCallback((row: RowResult) => {
    setEditRow(row);
  }, []);

  const onSavedCorrection = useCallback(
    (row: RowResult, correctedCth: string, correctedBcd: number | null) => {
      setRows((prev) =>
        prev
          ? prev.map((r) =>
              r.row_idx === row.row_idx
                ? {
                    ...r,
                    final: {
                      ...r.final,
                      cth_final: correctedCth,
                      bcd_final: correctedBcd ?? r.final.bcd_final,
                      reasoning:
                        (r.final.reasoning || "") +
                        `\n\n[Reviewer correction] CTH set to ${correctedCth}` +
                        (correctedBcd != null ? ` @ ${correctedBcd}% BCD` : ""),
                    },
                  }
                : r
            )
          : prev
      );
    },
    []
  );

  const onReevaluate = useCallback(
    async (row: RowResult) => {
      setBusyRows((s) => new Set(s).add(row.row_idx));
      try {
        const res = await api.reevaluate({
          item_description: row.item_description,
          declared_cth: row.declared_cth,
          declared_bcd: row.declared_bcd ?? undefined,
          top_k: 8,
          strict: true,
        });
        // If backend returns a full classification, patch the row in place.
        const cls = res.classification;
        if (cls) {
          setRows((prev) =>
            prev
              ? prev.map((r) =>
                  r.row_idx === row.row_idx
                    ? {
                        ...r,
                        final: {
                          ...r.final,
                          cth_final: cls.cth_final ?? r.final.cth_final,
                          bcd_final: cls.bcd_final ?? r.final.bcd_final,
                          confidence: cls.confidence ?? r.final.confidence,
                          reasoning: cls.reasoning ?? r.final.reasoning,
                          tier_used: "stage3_adjudicated",
                          candidates: res.candidates ?? r.final.candidates,
                        },
                      }
                    : r
                )
              : prev
          );
        } else {
          // Just got candidates back — patch top candidate as suggested.
          setRows((prev) =>
            prev
              ? prev.map((r) =>
                  r.row_idx === row.row_idx
                    ? { ...r, final: { ...r.final, candidates: res.candidates ?? r.final.candidates } }
                    : r
                )
              : prev
          );
        }
      } catch (e) {
        // Soft-fail; surface in chat drawer.
        setChatRow(row);
        setChatOpen(true);
      } finally {
        setBusyRows((s) => {
          const next = new Set(s);
          next.delete(row.row_idx);
          return next;
        });
      }
    },
    []
  );

  return (
    <div className="container-wide pt-8 pb-24">
      {/* Header */}
      <div className="mb-6 flex items-start justify-between gap-4 animate-fade-in">
        <div className="min-w-0">
          <Link href="/jobs" className="inline-flex items-center gap-1 text-xs text-ink-muted hover:text-ink">
            <ArrowLeft className="h-3 w-3" /> Back to audits
          </Link>
          <h1 className="mt-2 font-serif text-[36px] leading-tight tracking-tight truncate">
            {filename || "Audit results"}
          </h1>
          <p className="mt-1 text-xs text-ink-muted">
            Job <code className="font-mono">{jobId}</code>
            {completedAt ? ` · completed ${timeAgo(completedAt)}` : ""}
            {!completedAt && status
              ? ` · ${status} · ${processed.toLocaleString()} / ${totalRows ? totalRows.toLocaleString() : "?"} rows`
              : ""}
          </p>
        </div>
        <div className="shrink-0 flex items-center gap-2">
          <button
            onClick={() => {
              setChatRow(null);
              setChatOpen(true);
            }}
            className="btn-secondary text-sm"
          >
            <MessageSquare className="h-3.5 w-3.5" />
            Ask AI
          </button>
          <button
            type="button"
            disabled={status !== "done"}
            onClick={async () => {
              try {
                await api.downloadAnnotated(jobId, filename || `audit_${jobId}`);
              } catch (e) {
                setToast(e instanceof Error ? e.message : String(e));
              }
            }}
            className={cn("btn-primary text-sm", status !== "done" && "opacity-50")}
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6 animate-slide-up">
        {!stats ? (
          [0, 1, 2, 3].map((i) => <div key={i} className="skeleton h-24 rounded-2xl" />)
        ) : (
          <>
            <StatCard label="Total rows" value={stats.total.toLocaleString()} hint={`${stats.manualQueue} need manual review`} />
            <StatCard
              label="Fraud flags"
              value={stats.fraud.toLocaleString()}
              tone={stats.fraud > 0 ? "danger" : "neutral"}
              hint={`${stats.cthMismatch} CTH mismatches`}
            />
            <StatCard
              label="Revenue at risk"
              value={formatINR(stats.totalRisk)}
              tone={stats.totalRisk > 0 ? "danger" : "neutral"}
              hint="BCD under-declaration"
            />
            <StatCard
              label="Coverage"
              value={`${stats.total ? Math.round(((stats.total - 0) / stats.total) * 100) : 0}%`}
              tone="success"
              hint="rows classified"
            />
          </>
        )}
      </div>

      {/* Toolbar */}
      <div className="mb-3 flex flex-wrap items-center gap-2 animate-fade-in">
        <div className="relative flex-1 min-w-[280px]">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-muted" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter description, CTH, reasoning…"
            className="input pl-9"
          />
        </div>
        <button
          onClick={() => setFraudOnly((v) => !v)}
          className={cn(
            "btn-secondary text-sm transition",
            fraudOnly && "bg-danger/10 text-danger border-danger/20 hover:bg-danger/15"
          )}
        >
          <Filter className="h-3.5 w-3.5" />
          {fraudOnly ? "Fraud only" : "All rows"}
        </button>
      </div>

      {/* Body */}
      {error ? (
        <div className="surface px-5 py-12 text-center">
          <p className="text-sm text-danger">{error}</p>
        </div>
      ) : !rows ? (
        <div className="space-y-2">
          <div className="skeleton h-12 rounded-2xl" />
          <div className="skeleton h-12 rounded-xl" />
          <div className="skeleton h-12 rounded-xl" />
          <div className="skeleton h-12 rounded-xl" />
        </div>
      ) : (
        <div className="animate-fade-in">
          <ResultsTable
            rows={rows}
            filter={filter}
            fraudOnly={fraudOnly}
            onAskAi={onAskAi}
            onReevaluate={onReevaluate}
            onEdit={onEdit}
            busyRows={busyRows}
          />
        </div>
      )}

      {/* Floating ask AI button (mobile + general) */}
      <button
        onClick={() => {
          setChatRow(null);
          setChatOpen(true);
        }}
        className="fixed bottom-6 right-6 z-30 inline-flex items-center gap-2 rounded-full bg-ink px-4 py-3 text-sm font-medium text-white shadow-lift transition-transform hover:-translate-y-0.5 active:translate-y-0"
      >
        <MessageSquare className="h-4 w-4" />
        Ask AI
      </button>

      <ChatDrawer
        open={chatOpen}
        onClose={() => setChatOpen(false)}
        contextRow={chatRow}
      />

      <CorrectionDialog
        row={editRow}
        jobId={jobId}
        onClose={() => setEditRow(null)}
        onSaved={onSavedCorrection}
      />

      {toast ? (
        <div
          role="status"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-start gap-2 max-w-md rounded-xl border border-danger/20 bg-surface px-4 py-3 shadow-lift animate-slide-up"
        >
          <AlertTriangle className="h-4 w-4 mt-0.5 text-danger shrink-0" />
          <span className="text-[13px] text-ink-soft">{toast}</span>
        </div>
      ) : null}
    </div>
  );
}
