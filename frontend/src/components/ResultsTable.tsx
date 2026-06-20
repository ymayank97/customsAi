"use client";

import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useMemo, useState } from "react";
import {
  ArrowUpDown,
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  MessageSquare,
  Pencil,
  RefreshCw,
  ShieldAlert,
  Info,
  X,
  ArrowRight,
} from "lucide-react";
import type { AuditTrace, ClassificationBreakdown, RowResult } from "@/lib/api";
import { cn, formatINR, formatPercent, truncate } from "@/lib/utils";

const TIER_DETAIL: Record<string, { label: string; help: string; cls: string }> = {
  stage3_adjudicated: {
    label: "AI checked",
    help: "The AI read the description, applied the tariff rules, and confirmed this code against the official tariff.",
    cls: "bg-success/10 text-success",
  },
  stage4_validated: {
    label: "Corrected",
    help: "The AI's first suggestion was corrected to match a real code and rate in the official tariff.",
    cls: "bg-accent-soft text-accent",
  },
  stage1_fallback: {
    label: "Best match",
    help: "The AI couldn't run, so we show the closest matching tariff code from the search.",
    cls: "bg-warn/10 text-warn",
  },
  manual_queue: {
    label: "Needs your review",
    help: "We couldn't confirm a code with confidence — please review this line yourself.",
    cls: "bg-danger/10 text-danger",
  },
};

type Props = {
  rows: RowResult[];
  filter: string;
  fraudOnly: boolean;
  onAskAi: (row: RowResult) => void;
  onReevaluate: (row: RowResult) => void;
  onEdit?: (row: RowResult) => void;
  busyRows: Set<number>;
};

export function ResultsTable({
  rows,
  filter,
  fraudOnly,
  onAskAi,
  onReevaluate,
  onEdit,
  busyRows,
}: Props) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [selectedRowIdx, setSelectedRowIdx] = useState<number | null>(null);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return rows.filter((r) => {
      if (fraudOnly && !r.fraud.is_fraud) return false;
      if (!q) return true;
      const hay =
        (r.item_description || "") +
        " " +
        (r.declared_cth || "") +
        " " +
        (r.final?.cth_final || "") +
        " " +
        (r.final?.reasoning || "");
      return hay.toLowerCase().includes(q);
    });
  }, [rows, filter, fraudOnly]);

  const columns = useMemo<ColumnDef<RowResult>[]>(
    () => [
      {
        accessorKey: "item_description",
        header: "Description",
        cell: ({ row }) => (
          <button
            onClick={() => setSelectedRowIdx(row.original.row_idx)}
            className="text-left max-w-[640px] w-full"
            title={row.original.item_description}
          >
            <div className="text-[13px] leading-snug line-clamp-2 text-ink">
              {row.original.item_description || "—"}
            </div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-ink-muted/80">
              <span>Row {row.original.row_idx + 1}</span>
              {row.original.be_no && <span>· BE {row.original.be_no}</span>}
              {row.original.importer && (
                <span className="truncate max-w-[260px]">· {row.original.importer}</span>
              )}
            </div>
          </button>
        ),
        size: 580,
      },
      {
        id: "declared",
        header: "Declared",
        cell: ({ row }) => (
          <div className="text-[12px] leading-tight">
            <div className="font-mono text-ink">{row.original.declared_cth || "—"}</div>
            <div className="text-ink-muted">BCD {formatPercent(row.original.declared_bcd)}</div>
          </div>
        ),
      },
      {
        id: "recommended",
        header: "Recommended",
        cell: ({ row }) => {
          const r = row.original;
          const declaredBcd = r.declared_bcd;
          const finalBcd = r.final?.bcd_final ?? null;
          const delta =
            declaredBcd != null && finalBcd != null
              ? Math.round((finalBcd - declaredBcd) * 100) / 100
              : null;
          return (
            <div className="text-[12px] leading-tight">
              <div className="font-mono font-semibold text-ink">
                {r.final?.cth_final || "—"}
              </div>
              <div className="flex items-baseline gap-1.5">
                <span className="text-ink-muted">BCD {formatPercent(finalBcd)}</span>
                {delta !== null && delta !== 0 && (
                  <span
                    className={cn(
                      "rounded px-1 py-0 text-[10px] font-medium tabular-nums",
                      delta > 0 ? "bg-danger/10 text-danger" : "bg-page text-ink-muted"
                    )}
                  >
                    {delta > 0 ? "+" : ""}
                    {delta}%
                  </span>
                )}
              </div>
            </div>
          );
        },
      },
      {
        id: "flag",
        header: "Flag",
        accessorFn: (r) => (r.fraud?.is_fraud ? 2 : r.fraud?.flag_type ? 1 : 0),
        cell: ({ row }) => <FlagBadge flag={row.original.fraud} />,
        size: 160,
      },
      {
        id: "review",
        header: "Review",
        accessorFn: (r) => (r.review_flags?.length ? 1 : 0),
        cell: ({ row }) => <ReviewBadge row={row.original} />,
        size: 120,
      },
      {
        id: "risk",
        header: "Risk",
        accessorFn: (r) => r.fraud?.revenue_at_risk ?? 0,
        cell: ({ row }) => (
          <span
            className={cn(
              "font-mono text-[12px] tabular-nums",
              row.original.fraud?.is_fraud && row.original.fraud?.revenue_at_risk
                ? "text-danger font-semibold"
                : "text-ink-muted"
            )}
          >
            {formatINR(row.original.fraud?.revenue_at_risk)}
          </span>
        ),
        size: 130,
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => {
          const busy = busyRows.has(row.original.row_idx);
          return (
            <div className="flex items-center gap-1 justify-end">
              <button
                onClick={() => setSelectedRowIdx(row.original.row_idx)}
                className="btn-ghost h-7 w-7 p-0"
                title="Open details"
              >
                <Info className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={() => onReevaluate(row.original)}
                className="btn-ghost h-7 w-7 p-0"
                title="Re-evaluate"
                disabled={busy}
              >
                <RefreshCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
              </button>
              {onEdit && (
                <button
                  onClick={() => onEdit(row.original)}
                  className="btn-ghost h-7 w-7 p-0"
                  title="Correct CTH (saves as labeled feedback)"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                onClick={() => onAskAi(row.original)}
                className="btn-ghost h-7 w-7 p-0"
                title="Ask AI about this row"
              >
                <MessageSquare className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        },
        size: 140,
      },
    ],
    [onAskAi, onReevaluate, onEdit, busyRows]
  );

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 50 } },
  });

  const selectedRow = rows.find((r) => r.row_idx === selectedRowIdx) ?? null;

  return (
    <>
      <div className="surface overflow-hidden">
        <div className="overflow-auto max-h-[68vh]">
          <table className="w-full text-sm" style={{ minWidth: 1080 }}>
            <thead className="sticky top-0 z-10 bg-page/95 backdrop-blur">
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id} className="border-b border-line">
                  {hg.headers.map((h) => {
                    const sortable = h.column.getCanSort() && h.column.id !== "actions";
                    const sorted = h.column.getIsSorted();
                    return (
                      <th
                        key={h.id}
                        className="px-3 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-ink-muted whitespace-nowrap"
                      >
                        {sortable ? (
                          <button
                            onClick={h.column.getToggleSortingHandler()}
                            className="inline-flex items-center gap-1 hover:text-ink"
                          >
                            {flexRender(h.column.columnDef.header, h.getContext())}
                            {sorted === "asc" ? (
                              <ArrowUp className="h-3 w-3" />
                            ) : sorted === "desc" ? (
                              <ArrowDown className="h-3 w-3" />
                            ) : (
                              <ArrowUpDown className="h-3 w-3 opacity-40" />
                            )}
                          </button>
                        ) : (
                          flexRender(h.column.columnDef.header, h.getContext())
                        )}
                      </th>
                    );
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.length === 0 ? (
                <tr>
                  <td
                    colSpan={columns.length}
                    className="px-4 py-16 text-center text-sm text-ink-muted"
                  >
                    No rows match the current filters.
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((r) => (
                  <tr
                    key={r.id}
                    className={cn(
                      "border-b border-line/60 transition-colors cursor-pointer",
                      r.original.fraud?.is_fraud && "bg-danger/[.06]",
                      selectedRowIdx === r.original.row_idx &&
                        "outline outline-1 outline-accent/40",
                      "hover:bg-page/60"
                    )}
                    onClick={(ev) => {
                      // Only open detail if the click wasn't on an action button.
                      if (
                        ev.target instanceof HTMLElement &&
                        ev.target.closest("button")
                      ) {
                        return;
                      }
                      setSelectedRowIdx(r.original.row_idx);
                    }}
                  >
                    {r.getVisibleCells().map((cell) => (
                      <td
                        key={cell.id}
                        className="px-3 py-2.5 align-top whitespace-nowrap"
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <div className="flex items-center justify-between border-t border-line px-4 py-2.5 bg-page/40">
          <div className="text-xs text-ink-muted">
            {filtered.length.toLocaleString()} row{filtered.length === 1 ? "" : "s"} ·{" "}
            page {table.getState().pagination.pageIndex + 1} / {table.getPageCount() || 1}
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="btn-ghost h-7 w-7 p-0"
            >
              <ChevronLeft className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="btn-ghost h-7 w-7 p-0"
            >
              <ChevronRight className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      </div>

      <RowDetailDrawer
        row={selectedRow}
        onClose={() => setSelectedRowIdx(null)}
        onAskAi={onAskAi}
        onReevaluate={onReevaluate}
        onEdit={onEdit}
        busy={selectedRow ? busyRows.has(selectedRow.row_idx) : false}
      />
    </>
  );
}

function TierTag({ tier }: { tier?: RowResult["final"]["tier_used"] }) {
  const t = TIER_DETAIL[tier ?? ""] ?? {
    label: "Tier —",
    cls: "bg-page text-ink-muted",
    help: "Tier not recorded",
  };
  return (
    <span
      title={t.help}
      className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${t.cls}`}
    >
      {t.label}
    </span>
  );
}

function ReviewBadge({ row }: { row: RowResult }) {
  const flags = row.review_flags ?? [];
  if (!flags.length && !row.retrieval_diagnostics?.ambiguous) {
    return <span className="text-[12px] text-ink-muted">—</span>;
  }
  const labels = flags.length
    ? flags
    : row.retrieval_diagnostics?.ambiguous
      ? ["ambiguous_retrieval"]
      : [];
  return (
    <span
      className="inline-flex items-center rounded-full bg-warn/15 px-2 py-0.5 text-[10px] font-semibold text-warn ring-1 ring-warn/25"
      title={labels.join(", ")}
    >
      {labels.includes("ambiguous_retrieval") ? "Ambiguous retrieval" : labels.join(", ")}
    </span>
  );
}

function FlagBadge({ flag }: { flag: RowResult["fraud"] }) {
  if (!flag) return <span className="text-[12px] text-ink-muted">—</span>;
  if (flag.is_fraud) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-danger/10 px-2 py-0.5 text-[11px] font-semibold text-danger">
        <ShieldAlert className="h-3 w-3" />
        {flag.flag_type || "Fraud"}
      </span>
    );
  }
  if (!flag.flag_type) {
    return <span className="text-[12px] text-ink-muted">Clear</span>;
  }
  return (
    <span className="inline-flex items-center rounded-full bg-page px-2 py-0.5 text-[11px] font-medium text-ink-muted ring-1 ring-line">
      {flag.flag_type}
    </span>
  );
}

// --- Side panel --------------------------------------------------------------

function RowDetailDrawer({
  row,
  onClose,
  onAskAi,
  onReevaluate,
  onEdit,
  busy,
}: {
  row: RowResult | null;
  onClose: () => void;
  onAskAi: (row: RowResult) => void;
  onReevaluate: (row: RowResult) => void;
  onEdit?: (row: RowResult) => void;
  busy: boolean;
}) {
  return (
    <>
      <div
        className={cn(
          "fixed inset-0 z-40 bg-ink/15 backdrop-blur-[2px] transition-opacity duration-200",
          row ? "opacity-100" : "opacity-0 pointer-events-none"
        )}
        onClick={onClose}
      />
      <aside
        className={cn(
          "fixed right-0 top-0 z-50 h-full w-full max-w-[600px] bg-surface border-l border-line shadow-lift",
          "flex flex-col transition-transform duration-300 ease-[cubic-bezier(.2,.8,.2,1)]",
          row ? "translate-x-0" : "translate-x-full"
        )}
      >
        {row && <DrawerBody row={row} onClose={onClose} onAskAi={onAskAi} onReevaluate={onReevaluate} onEdit={onEdit} busy={busy} />}
      </aside>
    </>
  );
}

function DrawerBody({
  row,
  onClose,
  onAskAi,
  onReevaluate,
  onEdit,
  busy,
}: {
  row: RowResult;
  onClose: () => void;
  onAskAi: (row: RowResult) => void;
  onReevaluate: (row: RowResult) => void;
  onEdit?: (row: RowResult) => void;
  busy: boolean;
}) {
  const declaredBcd = row.declared_bcd;
  const finalBcd = row.final?.bcd_final ?? null;
  const delta =
    declaredBcd != null && finalBcd != null
      ? Math.round((finalBcd - declaredBcd) * 100) / 100
      : null;
  const cthChanged =
    !!row.final?.cth_final && row.declared_cth !== row.final?.cth_final;
  const conf = row.final?.confidence ?? 0;

  return (
    <>
      <header className="flex items-start justify-between gap-3 border-b border-line bg-surface px-5 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold leading-tight">
              Row {row.row_idx + 1}
            </h3>
            <TierTag tier={row.final?.tier_used} />
            <FlagBadge flag={row.fraud} />
          </div>
          <p className="mt-1 text-[11px] text-ink-muted">
            {row.be_no && (
              <>BE <span className="font-mono">{row.be_no}</span> · </>
            )}
            {row.importer || "Importer not in input"}
          </p>
        </div>
        <button onClick={onClose} className="btn-ghost h-8 w-8 p-0 shrink-0">
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="flex-1 overflow-auto bg-page/40 px-5 py-4 space-y-4">
        {/* Verdict — declared → recommended */}
        <section className="rounded-2xl border border-line bg-surface p-4">
          <div className="text-[10px] uppercase tracking-wider text-ink-muted">
            Verdict
          </div>
          <div className="mt-2 grid grid-cols-[1fr_auto_1fr] items-center gap-3">
            <VerdictPanel
              label="Declared"
              cth={row.declared_cth}
              bcd={declaredBcd}
              chapter={row.declared_chapter}
              tone="neutral"
            />
            <ArrowRight className="h-4 w-4 text-ink-muted" />
            <VerdictPanel
              label="Recommended"
              cth={row.final?.cth_final}
              bcd={finalBcd}
              chapter={row.recommended_chapter}
              tone={row.fraud?.is_fraud ? "danger" : cthChanged ? "warn" : "good"}
            />
          </div>

          <div className="mt-3 grid grid-cols-3 gap-2 text-[11px]">
            <Stat
              label="CTH change"
              value={cthChanged ? "Different" : "Same"}
              tone={cthChanged ? "warn" : "good"}
            />
            <Stat
              label="BCD delta"
              value={
                delta == null
                  ? "—"
                  : delta === 0
                    ? "0%"
                    : `${delta > 0 ? "+" : ""}${delta}%`
              }
              tone={delta == null ? "neutral" : delta > 0 ? "danger" : delta < 0 ? "warn" : "good"}
            />
            <Stat
              label="Confidence"
              value={`${Math.round(conf * 100)}%`}
              tone={conf > 0.8 ? "good" : conf > 0.5 ? "neutral" : "warn"}
            />
          </div>

          {row.fraud?.is_fraud && (
            <div className="mt-3 rounded-lg border border-danger/20 bg-danger/5 px-3 py-2 text-xs leading-relaxed text-danger">
              <div className="font-semibold">Fraud — duty under-declared</div>
              <div className="mt-0.5">{row.fraud.detail}</div>
              {row.fraud.revenue_at_risk != null && (
                <div className="mt-1 font-mono">
                  Revenue at risk: <span className="font-bold">{formatINR(row.fraud.revenue_at_risk)}</span>
                  {row.assess_val != null && (
                    <span className="ml-2 text-danger/70">on assess val {formatINR(row.assess_val)}</span>
                  )}
                </div>
              )}
            </div>
          )}
          {!row.fraud?.is_fraud && row.fraud?.flag_type && (
            <div className="mt-3 rounded-lg border border-line bg-page/40 px-3 py-2 text-xs leading-relaxed text-ink-muted">
              <div className="font-medium text-ink">{row.fraud.flag_type}</div>
              <div className="mt-0.5">{row.fraud.detail}</div>
            </div>
          )}
          {(row.review_flags?.length || row.retrieval_diagnostics?.ambiguous) ? (
            <div className="mt-3 rounded-lg border border-warn/30 bg-warn/5 px-3 py-2 text-xs leading-relaxed text-ink">
              <div className="font-semibold text-warn">Manual review suggested</div>
              <ul className="mt-1 list-disc pl-4 space-y-0.5">
                {(row.review_flags ?? []).map((f) => (
                  <li key={f}>{f.replace(/_/g, " ")}</li>
                ))}
              </ul>
              {row.retrieval_diagnostics?.margin != null && (
                <div className="mt-1 text-ink-muted">
                  Rerank margin (top1 − top2): {row.retrieval_diagnostics.margin}
                </div>
              )}
            </div>
          ) : null}
        </section>

        {row.rate_resolution?.detail && (
          <section className="rounded-xl border border-line bg-surface p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted">
              BCD resolution (variants + conditions)
            </div>
            <div className="mt-2 text-xs leading-relaxed text-ink">
              <div className="font-medium">
                Basis:{" "}
                <span className="font-mono">{row.rate_resolution.bcd_basis ?? "—"}</span>
                {row.rate_resolution.misuse_suspected ? (
                  <span className="ml-2 rounded bg-danger/10 px-1.5 py-0.5 text-[10px] font-semibold text-danger">
                    misuse suspected
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-ink-muted">{row.rate_resolution.detail}</p>
            </div>
          </section>
        )}

        {/* Classification trail — chapter -> heading -> 8-digit hierarchy */}
        {row.final?.breakdown && (
          <ClassificationTrail breakdown={row.final.breakdown} />
        )}

        {/* Full description */}
        <section className="rounded-xl border border-line bg-surface p-4">
          <div className="text-[10px] uppercase tracking-wider text-ink-muted">
            Item description
          </div>
          <div className="mt-1 text-sm leading-relaxed text-ink">
            {row.item_description || "—"}
          </div>
          {row.assess_val != null && (
            <div className="mt-2 text-[11px] text-ink-muted">
              Assessable value: <span className="font-mono">{formatINR(row.assess_val)}</span>
            </div>
          )}
        </section>

        {/* BCD breakdown card */}
        <section className="rounded-xl border border-line bg-surface p-4">
          <div className="text-[10px] uppercase tracking-wider text-ink-muted">
            BCD breakdown
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
            <KV label="Statutory" value={formatPercent(row.bcd_breakdown?.statutory)} />
            <KV
              label="Effective"
              value={formatPercent(row.bcd_breakdown?.effective ?? row.final?.bcd_final)}
            />
            {row.unit && <KV label="Unit" value={row.unit} />}
            {row.import_policy && <KV label="Import policy" value={row.import_policy} />}
            {row.export_policy && <KV label="Export policy" value={row.export_policy} />}
            {row.declared_notification && (
              <KV label="Declared notification" value={row.declared_notification} mono />
            )}
          </div>
          {row.bcd_breakdown?.schedule_text && (
            <div className="mt-2 rounded-lg bg-page/40 px-2.5 py-1.5 text-[11px] text-ink-muted">
              <span className="font-semibold text-ink">Schedule:</span>{" "}
              {row.bcd_breakdown.schedule_text}
            </div>
          )}
          {row.bcd_breakdown?.notification_text && (
            <div className="mt-2 rounded-lg bg-page/40 px-2.5 py-1.5 text-[11px] text-ink-muted">
              <span className="font-semibold text-ink">Notification text:</span>{" "}
              {row.bcd_breakdown.notification_text}
            </div>
          )}
        </section>

        {/* Notifications */}
        {row.notification_details && row.notification_details.length > 0 && (
          <section className="rounded-xl border border-line bg-surface p-4">
            <div className="text-[10px] uppercase tracking-wider text-ink-muted">
              Applicable notifications
            </div>
            <div className="mt-2 space-y-1.5">
              {row.notification_details.slice(0, 10).map((n) => (
                <div
                  key={n.id}
                  className="rounded-lg border border-line bg-page/50 px-2.5 py-2 text-xs"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono font-semibold">{n.id}</span>
                    {n.date && <span className="text-ink-muted">{n.date}</span>}
                  </div>
                  {n.title && (
                    <div className="mt-0.5 text-ink-muted">
                      {truncate(n.title, 140)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Reasoning */}
        <section className="rounded-xl border border-line bg-surface p-4">
          <div className="text-[10px] uppercase tracking-wider text-ink-muted">
            AI reasoning
          </div>
          <div className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">
            {row.final?.reasoning || "—"}
          </div>
          {row.final?.disagreement && (
            <div className="mt-3 rounded-lg border border-warn/20 bg-warn/5 px-3 py-2 text-xs leading-relaxed text-warn">
              <span className="font-semibold">Validator override: </span>
              {row.final.disagreement}
            </div>
          )}
        </section>

        {/* Verification: GIR path, conformal set, legal docket, precedent, revenue basis */}
        <VerificationSection final={row.final} />

        {/* Audit trace */}
        {row.audit_trace && row.audit_trace.stages?.length > 0 && (
          <AuditTraceSection audit={row.audit_trace} />
        )}

        {/* Top candidates */}
        <section className="rounded-xl border border-line bg-surface p-4">
          <div className="text-[10px] uppercase tracking-wider text-ink-muted">
            Top candidates considered
          </div>
          <div className="mt-2 space-y-1.5">
            {(row.final?.candidates ?? []).slice(0, 8).map((c) => {
              const isFinal = c.cth === row.final?.cth_final;
              return (
                <div
                  key={c.cth}
                  className={cn(
                    "rounded-lg border px-2.5 py-2 text-xs",
                    isFinal ? "border-success/40 bg-success/5" : "border-line bg-page/50"
                  )}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono font-semibold">{c.cth}</span>
                      {isFinal && (
                        <span className="rounded bg-success/10 px-1 py-0 text-[9px] font-semibold uppercase tracking-wider text-success">
                          Final
                        </span>
                      )}
                    </div>
                    <span className="text-ink-muted">
                      BCD {formatPercent(c.bcd_effective ?? c.bcd_statutory)}
                    </span>
                  </div>
                  <div className="mt-0.5 text-ink-muted">
                    {c.chapter_no
                      ? `Ch. ${String(c.chapter_no).padStart(2, "0")} ${c.chapter_title || ""} · `
                      : ""}
                    {truncate(c.description, 120)}
                  </div>
                </div>
              );
            })}
            {(!row.final?.candidates || row.final.candidates.length === 0) && (
              <div className="text-xs text-ink-muted">No candidates retained.</div>
            )}
          </div>
        </section>
      </div>

      <footer className="flex items-center gap-2 border-t border-line bg-surface px-4 py-3">
        <button
          onClick={() => onReevaluate(row)}
          disabled={busy}
          className="btn-secondary flex-1 text-sm"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", busy && "animate-spin")} />
          Re-evaluate
        </button>
        {onEdit && (
          <button onClick={() => onEdit(row)} className="btn-secondary flex-1 text-sm">
            <Pencil className="h-3.5 w-3.5" />
            Correct
          </button>
        )}
        <button onClick={() => onAskAi(row)} className="btn-primary flex-1 text-sm">
          <MessageSquare className="h-3.5 w-3.5" />
          Ask AI
        </button>
      </footer>
    </>
  );
}

function VerdictPanel({
  label,
  cth,
  bcd,
  chapter,
  tone,
}: {
  label: string;
  cth?: string;
  bcd?: number | null;
  chapter?: RowResult["declared_chapter"];
  tone: "neutral" | "good" | "warn" | "danger";
}) {
  const toneCls = {
    neutral: "bg-page",
    good: "bg-success/5",
    warn: "bg-warn/5",
    danger: "bg-danger/5",
  }[tone];
  return (
    <div className={cn("rounded-xl border border-line p-3", toneCls)}>
      <div className="text-[10px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className="mt-1 font-mono text-sm font-bold">{cth || "—"}</div>
      <div className="text-[12px] text-ink-muted">BCD {formatPercent(bcd)}</div>
      {chapter?.no && (
        <div className="mt-1.5 text-[10px] text-ink-muted line-clamp-2">
          Ch. {chapter.no} · {chapter.title || ""}
        </div>
      )}
    </div>
  );
}

function ClassificationTrail({ breakdown }: { breakdown: ClassificationBreakdown }) {
  const attrs = breakdown.attributes || {};
  const attrEntries = Object.entries(attrs).filter(
    ([, v]) => v !== null && v !== undefined && String(v).trim() !== ""
  );
  const cth = `${breakdown.subheading_6 || ""}`.padEnd(8, " ").slice(0, 8);
  const last2 = cth.slice(6, 8).trim() || "??";

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wider text-ink-muted">
          Classification trail
        </div>
        {breakdown.is_residuary && (
          <span className="rounded bg-warn/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-warn">
            Residuary &quot;Other&quot;
          </span>
        )}
      </div>

      <ol className="mt-3 space-y-2">
        <TrailStep
          tag={`Chapter ${breakdown.chapter || "??"}`}
          title={breakdown.chapter_title || "—"}
        />
        <TrailStep
          tag={`Heading ${breakdown.heading || "????"}`}
          title={breakdown.heading_text || "—"}
        />
        {breakdown.subheading_text && (
          <TrailStep
            tag={`Sub-heading ${breakdown.subheading_6 || "??????"}`}
            title={breakdown.subheading_text}
          />
        )}
        <TrailStep
          tag={`Tariff item ${last2 === "??" ? "??" : last2}`}
          title={breakdown.leaf_text || "—"}
          highlight
        />
      </ol>

      {breakdown.discriminator && (
        <div className="mt-3 rounded-lg border border-line bg-page/40 p-3">
          <div className="text-[9px] uppercase tracking-wider text-ink-muted">
            Discriminator (legal rule)
          </div>
          <div className="mt-1 text-[12px] leading-relaxed text-ink">
            {breakdown.discriminator}
          </div>
        </div>
      )}

      {attrEntries.length > 0 && (
        <div className="mt-3">
          <div className="text-[9px] uppercase tracking-wider text-ink-muted">
            Attributes extracted
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {attrEntries.map(([k, v]) => (
              <span
                key={k}
                className="rounded-full bg-page/40 px-2 py-0.5 text-[10px] text-ink-muted"
              >
                <span className="font-medium text-ink">{k}:</span>{" "}
                {String(v).slice(0, 60)}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function TrailStep({
  tag,
  title,
  highlight,
}: {
  tag: string;
  title: string;
  highlight?: boolean;
}) {
  return (
    <li
      className={cn(
        "flex gap-2.5 rounded-lg border p-2.5 text-xs leading-snug",
        highlight ? "border-success/30 bg-success/5" : "border-line bg-page/40"
      )}
    >
      <span
        className={cn(
          "shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold",
          highlight ? "bg-success/15 text-success" : "bg-page text-ink-muted"
        )}
      >
        {tag}
      </span>
      <span className={cn("flex-1", highlight ? "text-ink" : "text-ink-muted")}>
        {title}
      </span>
    </li>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "neutral" | "good" | "warn" | "danger";
}) {
  const cls = {
    neutral: "bg-page text-ink",
    good: "bg-success/10 text-success",
    warn: "bg-warn/10 text-warn",
    danger: "bg-danger/10 text-danger",
  }[tone];
  return (
    <div className={cn("rounded-lg px-2.5 py-1.5", cls)}>
      <div className="text-[9px] uppercase tracking-wider opacity-70">{label}</div>
      <div className="font-semibold">{value}</div>
    </div>
  );
}

function KV({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-lg bg-page/40 px-2.5 py-1.5">
      <div className="text-[9px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className={cn("text-[12px] text-ink", mono && "font-mono")}>{value}</div>
    </div>
  );
}

const STAGE_TITLES: Record<string, string> = {
  enrichment: "Reading the line",
  candidate_generation: "Finding possible codes",
  legal_constraints: "Applying tariff rules",
  gir_adjudication: "AI classification",
  validation_precedent: "Checking tariff & past rulings",
  revenue_routing: "Duty & risk check",
};

function AuditTraceSection({ audit }: { audit: AuditTrace }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="text-[10px] uppercase tracking-wider text-ink-muted">
        How we checked this line
      </div>
      {audit.summary && (
        <div className="mt-1 text-xs leading-snug text-ink">{audit.summary}</div>
      )}
      <ol className="mt-3 space-y-2">
        {audit.stages.map((s, idx) => (
          <li
            key={`${s.stage}-${idx}`}
            className="rounded-lg border border-line bg-page/40 p-2.5 text-xs"
          >
            <div className="flex items-center justify-between">
              <span className="font-semibold text-ink">
                {STAGE_TITLES[s.stage] || s.stage}
              </span>
              {s.llm_failed && (
                <span className="rounded bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn">
                  LLM fallback
                </span>
              )}
            </div>
            {s.explanation && (
              <div className="mt-1 leading-relaxed text-ink-muted">{s.explanation}</div>
            )}
            {s.top_chapters && s.top_chapters.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {s.top_chapters.map((tc) => (
                  <span key={tc.chapter} className="chip">
                    {tc.chapter} · {Math.round(tc.prob * 100)}%
                  </span>
                ))}
              </div>
            )}
            {s.top_candidates && s.top_candidates.length > 0 && (
              <ul className="mt-1.5 space-y-0.5">
                {s.top_candidates.map((c) => (
                  <li key={c.cth} className="font-mono text-[11px] text-ink-muted">
                    {c.cth} · {c.score?.toFixed(2)} ·{" "}
                    <span className="font-sans">{truncate(c.description || "", 80)}</span>
                  </li>
                ))}
              </ul>
            )}
            {s.reasoning && (
              <div className="mt-1.5 whitespace-pre-wrap text-[11px] leading-snug text-ink-muted">
                {s.reasoning}
              </div>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function VerificationSection({ final }: { final: RowResult["final"] }) {
  const conformal = (final?.conformal_set ?? []).filter(Boolean);
  const docket = final?.legal_docket ?? [];
  const precedents = final?.precedent_citations ?? [];
  const rb = final?.revenue_basis;
  const gir = final?.gir_path;
  const hasAny =
    !!final?.manual_queue ||
    conformal.length > 0 ||
    docket.length > 0 ||
    precedents.length > 0 ||
    (!!rb && (rb.valid || rb.basis === "unsupported")) ||
    !!gir;
  if (!hasAny) return null;

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <div className="text-[10px] uppercase tracking-wider text-ink-muted">
        How we checked this
      </div>

      {final?.manual_queue && (
        <div className="mt-2 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger">
          We couldn&apos;t confirm a code with confidence — please review this
          line yourself.
        </div>
      )}

      {gir && (
        <div className="mt-2 text-xs">
          <span className="text-ink-muted">Steps followed: </span>
          <span className="font-mono text-ink">{gir}</span>
        </div>
      )}

      {conformal.length > 0 && (
        <div className="mt-2">
          <div className="text-[11px] text-ink-muted">
            Other possible codes {conformal.length > 1 ? "· needs a closer look" : "· clear match"}
          </div>
          <div className="mt-1 flex flex-wrap gap-1">
            {conformal.map((c) => (
              <span
                key={c}
                className={cn(
                  "rounded px-1.5 py-0.5 font-mono text-[11px]",
                  c === final?.cth_final
                    ? "bg-success/10 text-success"
                    : "bg-page/60 text-ink-muted"
                )}
              >
                {c}
              </span>
            ))}
          </div>
        </div>
      )}

      {rb && (rb.valid || rb.basis === "unsupported") && (
        <div className="mt-2 text-xs">
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
              rb.valid ? "bg-success/10 text-success" : "bg-warn/10 text-warn"
            )}
          >
            {rb.valid ? "Duty rate looks justified" : "Duty rate not justified"}
          </span>
          {rb.detail && <span className="ml-2 text-ink-muted">{rb.detail}</span>}
        </div>
      )}

      {docket.length > 0 && (
        <div className="mt-3">
          <div className="text-[11px] text-ink-muted">Tariff rules applied</div>
          <ul className="mt-1 space-y-1">
            {docket.map((d, i) => (
              <li
                key={`${d.rule_id}-${i}`}
                className="rounded-lg border border-line bg-page/40 px-2.5 py-1.5 text-[11px]"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono font-semibold text-ink">
                    {d.rule_id}
                  </span>
                  {d.kind && (
                    <span
                      className={cn(
                        "rounded px-1 py-0 text-[9px] font-semibold uppercase tracking-wider",
                        d.kind === "hard_exclusion"
                          ? "bg-danger/10 text-danger"
                          : "bg-accent-soft text-accent"
                      )}
                    >
                      {d.kind.replace(/_/g, " ")}
                    </span>
                  )}
                  {d.bears_on && d.bears_on.length > 0 && (
                    <span className="font-mono text-[10px] text-ink-muted">
                      → {d.bears_on.join(", ")}
                    </span>
                  )}
                </div>
                {d.citation && (
                  <div className="mt-0.5 leading-snug text-ink-muted">
                    {truncate(d.citation, 200)}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {precedents.length > 0 && (
        <div className="mt-3">
          <div className="text-[11px] text-ink-muted">Past rulings</div>
          <ul className="mt-1 space-y-1">
            {precedents.map((p, i) => (
              <li
                key={p.ruling_id ?? i}
                className="rounded-lg border border-line bg-page/40 px-2.5 py-1.5 text-[11px]"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-ink">
                    {truncate(p.title || p.ruling_id || "Ruling", 90)}
                  </span>
                  {p.outcome_cth && (
                    <span className="font-mono text-[10px] text-success">
                      → {p.outcome_cth}
                    </span>
                  )}
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[10px] text-ink-muted">
                  {p.authority && <span>{p.authority}</span>}
                  {p.competing_pair && (
                    <span className="font-mono">{p.competing_pair}</span>
                  )}
                  {(p.heading_overlap ?? 0) > 0 && (
                    <span className="text-success">on-point</span>
                  )}
                  {p.source_url && (
                    <a
                      href={p.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-accent underline"
                    >
                      source
                    </a>
                  )}
                </div>
                {p.legal_basis && (
                  <div className="mt-0.5 leading-snug text-ink-muted">
                    {truncate(p.legal_basis, 160)}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
