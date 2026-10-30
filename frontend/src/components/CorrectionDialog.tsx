"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, X } from "lucide-react";
import type { RowResult } from "@/lib/api";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  row: RowResult | null;
  jobId?: string;
  onClose: () => void;
  onSaved?: (row: RowResult, correctedCth: string, correctedBcd: number | null) => void;
};

export function CorrectionDialog({ row, jobId, onClose, onSaved }: Props) {
  const [correctedCth, setCorrectedCth] = useState("");
  const [correctedBcd, setCorrectedBcd] = useState<string>("");
  const [reasoning, setReasoning] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (row) {
      setCorrectedCth(row.final?.cth_final || (row.final?.suggested_cth?.length === 8 ? row.final.suggested_cth : "") || row.declared_cth || "");
      setCorrectedBcd(
        row.final?.bcd_final != null ? String(row.final.bcd_final) : ""
      );
      setReasoning("");
      setError(null);
      setSavedAt(null);
    }
  }, [row?.row_idx]);

  if (!row) return null;

  const submit = async () => {
    if (!correctedCth.trim()) {
      setError("Enter the correct CTH code.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const bcdNum = correctedBcd === "" ? null : Number(correctedBcd);
      await api.saveFeedback({
        job_id: jobId,
        row_idx: row.row_idx,
        item_description: row.item_description,
        declared_cth: row.declared_cth,
        predicted_cth: row.final?.cth_final || row.final?.suggested_cth,
        predicted_bcd: row.final?.bcd_final,
        corrected_cth: correctedCth.trim(),
        corrected_bcd: Number.isFinite(bcdNum as number) ? (bcdNum as number) : null,
        reasoning,
      });
      setSavedAt(Date.now());
      onSaved?.(row, correctedCth.trim(), Number.isFinite(bcdNum as number) ? (bcdNum as number) : null);
      setTimeout(onClose, 700);
    } catch (e: any) {
      setError(e?.message || "Failed to save feedback");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div
        className="fixed inset-0 z-[60] bg-ink/30 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div
        className={cn(
          "fixed left-1/2 top-1/2 z-[61] w-[min(540px,92vw)] -translate-x-1/2 -translate-y-1/2",
          "surface p-5"
        )}
      >
        <header className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold">Correct row {row.row_idx + 1}</h3>
            <p className="mt-0.5 text-xs text-ink-muted">
              Your correction is saved for review and inclusion in the next training dataset.
            </p>
          </div>
          <button onClick={onClose} className="btn-ghost h-8 w-8 p-0">
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="mt-4 rounded-xl border border-line bg-page/40 p-3">
          <div className="text-[11px] uppercase tracking-wider text-ink-muted">Description</div>
          <div className="mt-1 text-sm leading-snug">
            {row.item_description || "—"}
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
            <div>
              <span className="text-ink-muted">Declared:</span>{" "}
              <span className="font-mono">{row.declared_cth || "—"}</span>
            </div>
            <div>
              <span className="text-ink-muted">Model suggestion:</span>{" "}
              <span className="font-mono">{row.final?.cth_final || row.final?.suggested_cth || "—"}</span>
            </div>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3">
          <label className="text-xs">
            <span className="text-ink-muted">Correct CTH</span>
            <input
              value={correctedCth}
              onChange={(e) => setCorrectedCth(e.target.value)}
              className="mt-1 w-full rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm focus:border-accent focus:outline-none"
              placeholder="e.g. 84713010"
              autoFocus
            />
          </label>
          <label className="text-xs">
            <span className="text-ink-muted">Correct BCD %</span>
            <input
              value={correctedBcd}
              onChange={(e) => setCorrectedBcd(e.target.value)}
              type="number"
              step="0.01"
              className="mt-1 w-full rounded-lg border border-line bg-surface px-2 py-1.5 font-mono text-sm focus:border-accent focus:outline-none"
              placeholder="optional"
            />
          </label>
        </div>

        <label className="mt-3 block text-xs">
          <span className="text-ink-muted">Reasoning (cited rule, scope, exclusion, GIR)</span>
          <textarea
            value={reasoning}
            onChange={(e) => setReasoning(e.target.value)}
            rows={3}
            className="mt-1 w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-sm focus:border-accent focus:outline-none"
            placeholder="e.g. GIR 3(c) latest in numerical order; chapter note 1(d) excludes…"
          />
        </label>

        {error && (
          <div className="mt-3 rounded-lg border border-danger/20 bg-danger/5 px-3 py-2 text-xs text-danger">
            {error}
          </div>
        )}

        {savedAt && (
          <div className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-success/10 px-2.5 py-1 text-xs text-success">
            <CheckCircle2 className="h-3.5 w-3.5" />
            Saved as labeled feedback
          </div>
        )}

        <footer className="mt-5 flex items-center justify-end gap-2">
          <button onClick={onClose} className="btn-ghost text-sm" disabled={saving}>
            Cancel
          </button>
          <button onClick={submit} className="btn-primary text-sm" disabled={saving}>
            {saving ? "Saving…" : "Save correction"}
          </button>
        </footer>
      </div>
    </>
  );
}
