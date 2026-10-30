"use client";

import { useState } from "react";
import type { RowResult } from "@/lib/api";
import { formatINR, formatPercent } from "@/lib/utils";

export function ResultsTable({ rows, onEdit }: { rows: RowResult[]; onEdit: (row: RowResult) => void }) {
  const [expanded, setExpanded] = useState<number | null>(null);
  return <div className="surface overflow-x-auto">
    <table className="w-full text-left text-sm">
      <thead className="border-b border-line bg-page text-xs text-ink-muted">
        <tr>{["Description", "Declared", "Suggestion", "BCD match /100", "Fraud review", "Status", ""].map((label, i) =>
          <th className="p-3 font-medium" key={i}>{label}</th>)}</tr>
      </thead>
      <tbody>{rows.map(row => <ResultRow key={row.row_idx} row={row} expanded={expanded === row.row_idx}
        toggle={() => setExpanded(expanded === row.row_idx ? null : row.row_idx)} onEdit={() => onEdit(row)} />)}</tbody>
    </table>
    {!rows.length && <p className="p-8 text-center text-ink-muted">No rows to display.</p>}
  </div>;
}

function ResultRow({ row, expanded, toggle, onEdit }: {
  row: RowResult; expanded: boolean; toggle: () => void; onEdit: () => void;
}) {
  const final = row.final;
  const assessment = row.bcd_assessment;
  const needsReview = assessment?.requires_review ?? true;
  return <>
    <tr className={`border-b border-line align-top ${needsReview ? "bg-danger/10" : ""}`} data-review-required={needsReview}>
      <td className="p-3 max-w-sm"><button className="text-left hover:underline" onClick={toggle}
        aria-expanded={expanded}>{row.item_description || "Empty description"}</button></td>
      <td className="p-3 font-mono">{row.declared_cth || "—"}<div className="text-xs text-ink-muted">{formatPercent(row.declared_bcd)}</div></td>
      <td className="p-3 font-mono">{final.cth_final || final.suggested_cth || "Review required"}
        {final.suggested_cth && final.suggested_cth.length < 8 && <div className="text-xs font-sans text-ink-muted">{final.prediction_level} · item needs details</div>}
        {final.supported_prefix && !final.cth_final && final.supported_prefix !== final.suggested_cth && <div className="text-xs text-ink-muted">Parent: {final.supported_prefix}</div>}
        <div className="text-xs text-ink-muted">{formatPercent(final.bcd_final)}</div></td>
      <td className="p-3" title={assessment?.reason || "BCD comparison is unverified."}>
        <span className={`font-semibold ${needsReview ? "text-danger" : "text-success"}`}>{assessment?.score == null ? "— /100" : `${assessment.score}/100`}</span>
        <div className="text-xs">{assessment?.status === "match" ? "Rate match" : assessment?.status === "under_declared" ? "BCD too low" : assessment?.status === "over_declared" ? "BCD too high" : "Unverified"}</div>
      </td>
      <td className="p-3 text-xs" title={assessment?.reason}><span className={assessment?.fraud_review_priority === "High" ? "font-bold text-danger" : ""}>{assessment?.fraud_review_priority || "Review required"}</span></td>
      <td className="p-3 text-xs">{final.manual_queue ? final.suggested_cth && final.suggested_cth.length < 8 ? "Needs specifications" : "Needs review" : final.acceptance_basis === "unique_positive_tariff_phrase" ? "Accepted · tariff name" : row.fraud.is_fraud ? "Potential discrepancy" : "Accepted · model"}
        {row.review_flags?.includes("rate_requires_review") && <div>Verify duty rate</div>}</td>
      <td className="p-3"><button className="btn-secondary text-xs" onClick={onEdit}>Correct</button></td>
    </tr>
    {expanded && <tr className={`border-b border-line ${needsReview ? "bg-danger/10" : "bg-page/40"}`}><td colSpan={7} className="p-4 space-y-3">
      <p>{final.reasoning}</p>
      <p className="text-sm">{assessment?.reason || "BCD comparison is unverified."}</p>
      <p className="text-xs text-ink-muted">The score measures agreement with a verified BCD reference. It is not a fraud probability. Red rows include uncertain results.</p>
      {!!final.candidates.length && <div><p className="text-xs text-ink-muted">Candidate paths for review</p>
        <ul className="mt-1 space-y-1">{final.candidates.map(c => <li key={c.cth}>
          <span className="font-mono">{c.cth}</span> · {c.description} · score {c.score.toPrecision(3)}
        </li>)}</ul></div>}
      {row.fraud.flag_type && <p>{row.fraud.detail} · {row.fraud.flag_type} · {formatINR(row.fraud.revenue_at_risk)}</p>}
      <ol className="space-y-1 text-xs text-ink-muted">{row.audit_trace?.stages.map((step, i) =>
        <li key={i}>{step.explanation}</li>)}</ol>
    </td></tr>}
  </>;
}
