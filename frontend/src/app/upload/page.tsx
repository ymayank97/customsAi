"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  UploadCloud,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  FileSpreadsheet,
  X,
  ArrowRight,
} from "lucide-react";
import { api, type JobDetail } from "@/lib/api";
import { AuthGate } from "@/components/AuthGate";
import { cn } from "@/lib/utils";

type Phase = "idle" | "uploading" | "processing" | "done" | "error";

// Plain-language label for whatever stage the backend reports.
function phaseMessage(phase: string): string {
  switch (phase) {
    case "retrieve":
      return "Finding matching tariff codes…";
    case "tier3_llm":
      return "Reviewing each line…";
    case "done":
      return "Almost done…";
    default:
      return "Getting ready…";
  }
}

function fmtEta(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds <= 0) return "";
  const s = Math.round(seconds);
  if (s < 60) return `about ${s} sec left`;
  const m = Math.floor(s / 60);
  return `about ${m} min left`;
}

export default function UploadPage() {
  return (
    <AuthGate>
      <Suspense
        fallback={
          <div className="container-narrow pt-12 pb-24">
            <div className="skeleton h-80 rounded-3xl" />
          </div>
        }
      >
        <UploadInner />
      </Suspense>
    </AuthGate>
  );
}

function UploadInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const resumeJob = searchParams.get("job");

  const [phase, setPhase] = useState<Phase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | null>(resumeJob);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-resume if ?job=ID
  useEffect(() => {
    if (!resumeJob) return;
    setPhase("processing");
    setJobId(resumeJob);
  }, [resumeJob]);

  // Poll job status
  useEffect(() => {
    if (!jobId || (phase !== "processing" && phase !== "uploading")) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const j = await api.jobStatus(jobId);
        if (!alive) return;
        setJob(j);
        if (j.status === "done") {
          setPhase("done");
          setTimeout(() => router.push(`/results/${jobId}`), 700);
          return;
        }
        if (j.status === "failed") {
          setPhase("error");
          setError(j.error || "Something went wrong while reviewing the file.");
          return;
        }
        timer = setTimeout(tick, 1200);
      } catch (e: any) {
        if (!alive) return;
        setPhase("error");
        setError(e?.message || "Lost connection to the server.");
      }
    };
    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, phase, router]);

  const handleFile = useCallback(async (f: File) => {
    if (!/\.xlsx?$/i.test(f.name)) {
      setError("Please choose an Excel file (.xlsx or .xls).");
      return;
    }
    const MAX_BYTES = 10 * 1024 * 1024;
    if (f.size > MAX_BYTES) {
      setError(`That file is ${(f.size / 1024 / 1024).toFixed(1)} MB. The limit is 10 MB.`);
      return;
    }
    if (f.size === 0) {
      setError("That file is empty.");
      return;
    }
    setFile(f);
    setError(null);
    setPhase("uploading");
    try {
      const { job_id } = await api.uploadFile(f);
      setJobId(job_id);
      setPhase("processing");
    } catch (e: any) {
      setError(e?.message || "Upload failed. Please try again.");
      setPhase("error");
    }
  }, []);

  const onDrop = (e: React.DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) void handleFile(f);
  };

  const onChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) void handleFile(f);
  };

  const reset = () => {
    setFile(null);
    setJobId(null);
    setJob(null);
    setError(null);
    setPhase("idle");
    if (inputRef.current) inputRef.current.value = "";
  };

  const total = job?.rows_total ?? 0;
  const processed = job?.rows_processed ?? 0;
  const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
  const phaseLabel = job?.progress?.phase ?? "";
  const eta = job?.progress?.eta_seconds ?? job?.eta_seconds ?? null;

  const stepNo: 1 | 2 | 3 =
    phase === "idle" || phase === "error" ? 1 : phase === "done" ? 3 : 2;

  return (
    <div className="container-narrow pt-12 pb-24">
      {/* Step indicator */}
      <StepIndicator step={stepNo} />

      {/* STEP 1 — choose file */}
      {(phase === "idle" || phase === "error") && (
        <div className="animate-slide-up">
          <h1 className="font-serif text-[34px] leading-tight tracking-tight">
            Choose your Bill of Entry
          </h1>
          <p className="mt-2 text-[15px] text-ink-soft">
            Pick the Excel file you want to check. It needs an{" "}
            <span className="font-medium text-ink">Item Description</span> column and a{" "}
            <span className="font-medium text-ink">Tariff Code (CTH)</span> column.
          </p>

          <label
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            className={cn(
              "mt-6 block cursor-pointer rounded-3xl border-2 border-dashed transition-all duration-200",
              dragOver
                ? "border-accent bg-accent-soft/50 ring-4 ring-accent/15"
                : "border-line2 bg-surface hover:border-ink/30"
            )}
          >
            <input
              ref={inputRef}
              type="file"
              accept=".xlsx,.xls"
              onChange={onChange}
              className="hidden"
            />
            <div className="px-8 py-16 text-center">
              <span className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-accent-soft text-accent">
                <UploadCloud className="h-7 w-7" />
              </span>
              <p className="mt-6 text-[20px] font-semibold tracking-tight">
                Drag your file here
              </p>
              <p className="mt-1.5 text-[14px] text-ink-muted">or</p>
              <span className="btn-primary-xl mt-4">
                <FileSpreadsheet className="h-5 w-5" />
                Select file
              </span>
              <p className="mt-6 text-[13px] text-ink-muted">
                Excel files (.xlsx, .xls) up to 10&nbsp;MB
              </p>
            </div>
          </label>

          {error && (
            <div className="mt-4 flex items-start gap-2.5 rounded-2xl border border-danger/20 bg-danger/5 px-4 py-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
              <p className="text-[14px] text-danger">{error}</p>
            </div>
          )}
        </div>
      )}

      {/* STEP 2 — uploading / reviewing */}
      {(phase === "uploading" || phase === "processing") && (
        <div className="surface p-7 animate-slide-up">
          <div className="flex items-start gap-3.5">
            <span className="mt-0.5 grid h-11 w-11 place-items-center rounded-xl bg-accent-soft text-accent">
              {phase === "uploading" ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <FileSpreadsheet className="h-5 w-5" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[17px] font-semibold">
                {job?.filename ?? file?.name ?? "Your file"}
              </div>
              <div className="mt-0.5 text-[14px] text-ink-soft">
                {phase === "uploading"
                  ? "Uploading your file…"
                  : phaseMessage(phaseLabel)}
              </div>
            </div>
          </div>

          <ProgressBar
            percent={phase === "uploading" ? null : pct}
            indeterminate={phase === "uploading" || total === 0}
          />

          <div className="mt-3 flex items-center justify-between text-[13px] text-ink-muted">
            <span>
              {total > 0
                ? `${processed.toLocaleString()} of ${total.toLocaleString()} lines checked`
                : "Preparing the file…"}
            </span>
            <span>{fmtEta(eta)}</span>
          </div>

          <p className="mt-5 text-[13px] text-ink-muted">
            You can leave this page open — we&apos;ll take you to the results
            automatically when it&apos;s ready.
          </p>

          {jobId && (
            <div className="mt-5">
              <a href={`/results/${jobId}`} className="btn-secondary text-sm">
                View lines checked so far
                <ArrowRight className="h-3.5 w-3.5" />
              </a>
            </div>
          )}
        </div>
      )}

      {/* STEP 3 — done */}
      {phase === "done" && (
        <div className="surface p-10 text-center animate-slide-up">
          <span className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-success/10 text-success">
            <CheckCircle2 className="h-7 w-7" />
          </span>
          <p className="mt-5 text-[20px] font-semibold">All done</p>
          <p className="mt-1.5 text-[14px] text-ink-muted">Opening your results…</p>
        </div>
      )}

      {/* Error footer for a failed job */}
      {phase === "error" && jobId && (
        <div className="mt-4 surface border-danger/20 p-6 animate-slide-up">
          <div className="flex items-start gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-lg bg-danger/10 text-danger">
              <AlertTriangle className="h-4 w-4" />
            </span>
            <div className="flex-1">
              <p className="text-[15px] font-medium text-danger">
                We couldn&apos;t finish that file
              </p>
              <p className="mt-1 text-[13px] text-ink-muted">{error}</p>
              <button onClick={reset} className="btn-secondary mt-4 text-sm">
                <X className="h-3.5 w-3.5" />
                Try another file
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StepIndicator({ step }: { step: 1 | 2 | 3 }) {
  const steps = [
    { n: 1, label: "Choose file" },
    { n: 2, label: "We review it" },
    { n: 3, label: "See results" },
  ];
  return (
    <div className="mb-8 flex items-center justify-center gap-3">
      <span className="text-[13px] font-semibold text-ink-muted">Step {step} of 3</span>
      <div className="flex items-center gap-1.5">
        {steps.map((s) => (
          <span
            key={s.n}
            className={cn(
              "step-dot",
              s.n === step && "step-dot-active",
              s.n < step && "step-dot-done"
            )}
            title={s.label}
          />
        ))}
      </div>
    </div>
  );
}

function ProgressBar({
  percent,
  indeterminate,
}: {
  percent: number | null;
  indeterminate?: boolean;
}) {
  return (
    <div className="mt-6">
      <div className="relative h-2.5 overflow-hidden rounded-full bg-line">
        {indeterminate ? (
          <div className="absolute inset-y-0 left-0 w-1/3 rounded-full bg-accent indet-bar" />
        ) : (
          <div
            className="absolute inset-y-0 left-0 rounded-full bg-accent transition-all duration-300 ease-out"
            style={{ width: `${percent ?? 0}%` }}
          />
        )}
      </div>
      <div className="mt-2 text-right text-[13px] font-medium tabular-nums text-ink-soft">
        {indeterminate ? "" : `${percent ?? 0}%`}
      </div>
    </div>
  );
}
