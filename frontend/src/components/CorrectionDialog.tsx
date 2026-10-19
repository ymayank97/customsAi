"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2, X } from "lucide-react";
import { api, type ReviewDecision, type RowResult, type TrainingLabel } from "@/lib/api";

type Props = {
  row: RowResult | null; jobId: string; onClose: () => void;
  onSaved?: (row: RowResult, review: TrainingLabel) => void;
};

export function CorrectionDialog({ row, jobId, onClose, onSaved }: Props) {
  const [decision, setDecision] = useState<ReviewDecision | "">("");
  const [code, setCode] = useState("");
  const [bcd, setBcd] = useState("");
  const [reference, setReference] = useState("");
  const [reasoning, setReasoning] = useState("");
  const [details, setDetails] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tariff, setTariff] = useState<{ code: string; text: string; error?: boolean } | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const controls = useRef({ saving, onClose });
  controls.current = { saving, onClose };
  const open = Boolean(row);
  const selectedCode = decision === "confirmed" ? row?.final.cth_final || "" : code;

  useEffect(() => {
    setDecision(row?.review?.decision || "");
    setCode(row?.review?.corrected_cth || "");
    setBcd(row?.review?.corrected_bcd != null ? String(row.review.corrected_bcd) : "");
    setReference(row?.review?.reference || "");
    setReasoning(row?.review?.reasoning || "");
    setDetails(row?.review?.product_details || "");
    setError(null); setSaved(false);
  }, [row, jobId]);

  useEffect(() => {
    if (!open) return;
    const prior = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !controls.current.saving) { event.preventDefault(); controls.current.onClose(); }
      if (event.key !== "Tab") return;
      const targets = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), a[href], summary') || []);
      const first = targets[0], last = targets[targets.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", keydown); prior?.focus(); };
  }, [open]);

  useEffect(() => {
    setTariff(null);
    if (!open || decision === "needs_information" || !/^[0-9]{8}$/.test(selectedCode)) return;
    let alive = true;
    const timer = setTimeout(() => {
      api.tariffCode(selectedCode).then(result => {
        if (alive) setTariff({ code: selectedCode, text: result.description || result.leaf_text || "Code found in the tariff master." });
      }).catch(() => { if (alive) setTariff({ code: selectedCode, text: "Code could not be verified. Check the code or try again.", error: true }); });
    }, 300);
    return () => { alive = false; clearTimeout(timer); };
  }, [selectedCode, decision, open]);

  if (!row) return null;
  const close = () => { if (!saving) onClose(); };
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!row || saving || !decision) { setError("Choose a review decision."); return; }
    if (decision !== "needs_information" && !/^[0-9]{8}$/.test(selectedCode)) { setError("Enter an eight-digit tariff code."); return; }
    if (decision !== "needs_information" && !reference.trim()) { setError("Add the document or tariff reference you checked."); return; }
    if (reasoning.trim().length < 3) { setError("Add a short note explaining your decision."); return; }
    const rate = bcd.trim() === "" ? null : Number(bcd);
    if (rate !== null && (!Number.isFinite(rate) || rate < 0)) { setError("Enter a valid non-negative BCD rate, or leave it blank."); return; }
    setSaving(true); setError(null);
    try {
      const response = await api.saveReview({
        job_id: jobId, row_idx: row.row_idx, decision,
        corrected_cth: decision === "needs_information" ? null : selectedCode,
        corrected_bcd: decision === "needs_information" ? null : rate,
        reference, reasoning, product_details: details,
      });
      onSaved?.(row, response.review);
      setSaved(true);
    } catch (issue) { setError(issue instanceof Error ? issue.message : "The review could not be saved. Please try again."); }
    finally { setSaving(false); }
  }

  return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-ink/30 p-4 backdrop-blur-[2px]" onClick={close}>
    <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="review-title"
      className="surface max-h-[92dvh] w-full max-w-xl overflow-y-auto p-5 sm:p-7" onClick={event => event.stopPropagation()}>
      <header className="flex items-start justify-between gap-3">
        <div><span className="text-xs font-semibold uppercase tracking-wide text-accent">Officer review</span>
          <h2 id="review-title" className="mt-1 text-xl font-semibold">Review item {row.row_idx + 1}</h2></div>
        <button type="button" disabled={saving} onClick={close} className="btn-ghost h-10 w-10 p-0" aria-label="Close review"><X className="h-5 w-5" /></button>
      </header>
      {saved ? <div className="py-8 text-center" role="status">
        <CheckCircle2 className="mx-auto h-12 w-12 text-success" />
        <h3 className="mt-4 text-xl font-semibold">Your review is saved</h3>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">{decision === "needs_information"
          ? "This item is waiting for more details and will stay out of the training export."
          : "Your verified code and reference are saved in Reviewed items. Conflicting labels are held out of the training export."}</p>
        <div className="mt-6 flex justify-center gap-2"><Link href="/reviewed-items" className="btn-secondary" onClick={close}>View reviewed items</Link><button onClick={close} className="btn-primary">Done</button></div>
      </div> : <form onSubmit={submit} className="mt-5 space-y-5" aria-busy={saving}>
        <div className="rounded-xl border border-line bg-page p-4">
          <p className="text-sm leading-relaxed">{row.item_description}</p>
          <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <span className="text-ink-soft">Declared <strong className="ml-1 font-mono text-ink">{row.declared_cth || "—"}</strong></span>
            <span className="text-ink-soft">Suggested <strong className="ml-1 font-mono text-ink">{row.final.cth_final || "—"}</strong></span>
          </div>
        </div>
        <fieldset disabled={saving}>
          <legend className="mb-2 text-sm font-semibold">What is your decision?</legend>
          <div className="space-y-2">
            {([
              ["confirmed", "Confirm the suggestion", "I verified the suggested tariff code."],
              ["corrected", "Correct the code", "A different tariff code applies."],
              ["needs_information", "More details needed", "I cannot classify this item from the available information."],
            ] as const).map(([value, title, detail]) => <label key={value} className={decision === value ? "flex cursor-pointer gap-3 rounded-xl border border-accent bg-accent-soft/40 p-3" : "flex cursor-pointer gap-3 rounded-xl border border-line p-3"}>
              <input type="radio" name="decision" value={value} checked={decision === value} onChange={() => setDecision(value)} className="mt-1 h-4 w-4 shrink-0 accent-blue-600" required />
              <span><span className="block text-sm font-medium">{title}</span><span className="mt-0.5 block text-xs leading-relaxed text-ink-muted">{detail}</span></span>
            </label>)}
          </div>
        </fieldset>
        {decision === "corrected" && <label className="block text-sm font-medium">Verified CTH (8 digits)
          <input className="input mt-2 font-mono" value={code} onChange={event => setCode(event.target.value.replace(/[^0-9]/g, "").slice(0, 8))} inputMode="numeric" maxLength={8} pattern="[0-9]{8}" placeholder="e.g. 84713010" disabled={saving} required />
          {code.length === 8 && <span className="mt-2 block text-xs text-ink-muted">Chapter {code.slice(0, 2)} › Heading {code.slice(0, 4)} › Subheading {code.slice(0, 6)} › Item {code}</span>}
        </label>}
        {decision && decision !== "needs_information" && /^[0-9]{8}$/.test(selectedCode) && <p className={tariff?.error ? "text-sm text-danger" : "rounded-lg bg-page p-3 text-sm text-ink-soft"} role="status">{tariff?.code === selectedCode ? tariff.text : "Checking tariff description…"}</p>}
        {decision !== "needs_information" && <label className="block text-sm font-medium">Document or tariff reference
          <input value={reference} onChange={event => setReference(event.target.value)} disabled={saving} className="input mt-2" maxLength={2000} placeholder="e.g. tariff note, invoice or ruling reference" required />
        </label>}
        <label className="block text-sm font-medium">{decision === "needs_information" ? "What information is missing?" : "Why does this code apply?"}
          <textarea value={reasoning} onChange={event => setReasoning(event.target.value)} disabled={saving} className="input mt-2" rows={3} minLength={3} maxLength={8000} required placeholder={decision === "needs_information" ? "e.g. Need the material composition and intended use." : "Briefly explain the product facts and rule you checked."} />
        </label>
        <details className="rounded-xl border border-line p-3">
          <summary className="cursor-pointer text-sm font-medium">Add product details or a verified duty rate (optional)</summary>
          <label className="mt-3 block text-sm">Extra product details<textarea className="input mt-2" value={details} onChange={event => setDetails(event.target.value)} rows={2} disabled={saving} maxLength={8000} placeholder="Material, function, composition or intended use from documents." /></label>
          {decision !== "needs_information" && <label className="mt-3 block text-sm">Verified Basic Customs Duty (%)<input className="input mt-2" value={bcd} onChange={event => setBcd(event.target.value)} type="number" min={0} step="any" disabled={saving} placeholder="Leave blank if not verified" /></label>}
        </details>
        <p className="text-xs leading-relaxed text-ink-muted">Save only decisions checked against product documents and tariff references. A suggested code alone is not a verified label.</p>
        {error && <p role="alert" className="rounded-lg bg-danger/5 p-3 text-sm text-danger">{error}</p>}
        <footer className="flex justify-end gap-2 border-t border-line pt-4"><button type="button" onClick={close} disabled={saving} className="btn-ghost">Cancel</button><button type="submit" disabled={saving || !decision} className="btn-primary">{saving ? "Saving review…" : row.review ? "Update review" : "Save review"}</button></footer>
      </form>}
    </div>
  </div>;
}
