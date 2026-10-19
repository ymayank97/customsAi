"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ClipboardCheck, Download, Search } from "lucide-react";
import { api, type ReviewList } from "@/lib/api";
import { useAuth } from "@/providers/AppProviders";
import { AuthGate } from "@/components/AuthGate";
import { StatCard } from "@/components/StatCard";

export default function ReviewedItemsPage() {
  return <AuthGate><ReviewedItems /></AuthGate>;
}

function ReviewedItems() {
  const { user } = useAuth();
  const [data, setData] = useState<ReviewList | null>(null);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  useEffect(() => {
    if (!user?.can_label_rows) return;
    let alive = true;
    setLoading(true); setError(null);
    const timer = setTimeout(() => {
      api.listReviews({ q: query, page }).then(result => { if (alive) setData(result); })
        .catch(issue => { if (alive) setError(issue instanceof Error ? issue.message : "Could not load reviewed items."); })
        .finally(() => { if (alive) setLoading(false); });
    }, 250);
    return () => { alive = false; clearTimeout(timer); };
  }, [user?.can_label_rows, page, query, attempt]);

  if (!user?.can_label_rows) return <div className="container-narrow py-12"><div className="surface p-8">
    <ClipboardCheck className="h-8 w-8 text-accent" /><h1 className="mt-4 text-2xl font-semibold">Premium reviewer access</h1>
    <p className="mt-3 text-ink-soft">Approved premium reviewers can save verified tariff decisions and export them for training. Ask your administrator to approve your account.</p>
    <Link href="/jobs" className="btn-primary mt-6">View your audits</Link>
  </div></div>;

  return <div className="container-wide py-10">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><span className="text-xs font-semibold uppercase tracking-wide text-accent">Officer review library</span>
        <h1 className="mt-2 font-serif text-4xl">Reviewed items</h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-soft">{user.is_admin ? "Verified decisions from all approved reviewers in this workspace." : "Your saved decisions, references and requests for more information."} These labels are stored for the next model training run.</p>
      </div>
      <button className="btn-primary" disabled={downloading || loading || !data?.summary.training_examples} onClick={async () => {
        setDownloading(true); setDownloadError(null);
        try { await api.downloadTrainingLabels(); }
        catch (issue) { setDownloadError(issue instanceof Error ? issue.message : "Could not download training labels."); }
        finally { setDownloading(false); }
      }}><Download className="h-4 w-4" />{downloading ? "Preparing export…" : "Export training labels (CSV)"}</button>
    </header>
    {data && <div className="mt-7 grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard label="Saved reviews" value={data.summary.reviewed} />
      <StatCard label="Training examples" value={data.summary.training_examples} hint="Verified, unique descriptions" tone="success" />
      <StatCard label="Need more details" value={data.summary.needs_information} hint="Excluded from training" tone="warn" />
      <StatCard label="Conflicting reviews" value={data.summary.conflicts} hint="Resolve before training" tone={data.summary.conflicts ? "danger" : "neutral"} />
    </div>}
    <div className="mt-6 rounded-xl border border-line bg-surface p-4 text-sm leading-relaxed text-ink-soft">The export includes verified codes with a reference and an officer note. Repeated descriptions are combined. If the same description has different verified codes, those reviews are held out until the difference is resolved or product details distinguish the items.</div>
    {data?.summary.conflicts ? <p className="mt-4 rounded-xl border border-warn/20 bg-warn/5 p-4 text-sm text-ink-soft">Open the audit to correct a conflicting review, add distinguishing product details, or mark the item as needing more information. Administrators can coordinate reviews from other officers.</p> : null}
    <div className="relative mt-6 max-w-xl"><Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-ink-muted" />
      <input className="input min-h-10 pl-10" aria-label="Search reviewed items" placeholder="Search description, verified code or reference" value={query} maxLength={200} onChange={event => { setQuery(event.target.value); setPage(1); }} />
    </div>
    {downloadError && <p role="alert" className="mt-4 text-sm text-danger">{downloadError}</p>}
    {error ? <div role="alert" className="surface mt-5 p-6"><p className="text-danger">{error}</p><button className="btn-secondary mt-4" onClick={() => setAttempt(value => value + 1)}>Try again</button></div> :
      loading ? <div role="status" aria-label="Loading reviews" className="mt-5 space-y-3">{[0, 1, 2].map(index => <div className="skeleton h-16 rounded-xl" key={index} />)}</div> :
      !data?.items.length ? <div className="surface mt-5 px-6 py-12 text-center"><ClipboardCheck className="mx-auto h-10 w-10 text-accent" />
        <h2 className="mt-4 text-lg font-semibold">{query ? "No reviews match your search" : "Your first review starts with an audit"}</h2>
        <p className="mt-2 text-sm text-ink-soft">{query ? "Try a product description or an eight-digit tariff code." : "Open a completed audit and choose Review item to save an officer decision."}</p>
        {!query && <Link href="/jobs" className="btn-primary mt-5">Go to past audits</Link>}
      </div> : <div className="surface mt-5 overflow-hidden">
        <div className="overflow-x-auto"><table className="w-full min-w-[1000px] text-left text-sm">
          <caption className="sr-only">Saved officer decisions and their suitability for training</caption>
          <thead className="border-b border-line bg-page text-xs text-ink-muted"><tr>
            {["Item description", "Verified code", "Original suggestion", "Decision", "Reference and note", "Training status"].map(title => <th key={title} scope="col" className="px-4 py-3 font-semibold">{title}</th>)}
          </tr></thead>
          <tbody>{data.items.map(review => <tr key={review.id} className="border-b border-line/60 align-top last:border-0">
            <td className="min-w-[240px] max-w-[360px] px-4 py-4"><p className="line-clamp-3 leading-relaxed">{review.item_description}</p>
              <p className="mt-2 text-xs text-ink-muted">Item {review.row_idx + 1} · {new Date(review.updated_at).toLocaleDateString()}</p>
              {review.user_id === user.id && <Link href={`/results/${review.job_id}`} className="mt-2 inline-block text-xs font-medium text-accent hover:underline">Open audit to edit review →</Link>}
            </td>
            <td className="px-4 py-4 font-mono font-semibold">{review.corrected_cth || "Pending"}{review.corrected_bcd != null && <div className="mt-1 text-xs font-normal text-ink-muted">BCD {review.corrected_bcd}%</div>}</td>
            <td className="px-4 py-4 font-mono text-ink-soft">{review.predicted_cth || "—"}</td>
            <td className="px-4 py-4"><p className="font-medium">{review.decision === "confirmed" ? "Confirmed" : review.decision === "corrected" ? "Corrected" : "More details needed"}</p><p className="mt-2 text-xs text-ink-muted">{review.reviewer_name}</p></td>
            <td className="min-w-[220px] max-w-[320px] px-4 py-4"><p className="break-words text-ink-soft">{review.reference || "Awaiting documents"}</p><details className="mt-2"><summary className="cursor-pointer text-xs font-medium text-accent">Review note</summary><p className="mt-2 whitespace-pre-wrap text-sm">{review.reasoning}</p>{review.product_details && <p className="mt-2 text-xs text-ink-muted">Product details: {review.product_details}</p>}</details></td>
            <td className="px-4 py-4"><span className={review.quality === "ready" ? "inline-flex rounded-full bg-success/10 px-3 py-1 text-xs font-medium text-success" : "inline-flex rounded-full bg-warn/10 px-3 py-1 text-xs font-medium text-warn"}>{review.quality === "ready" ? "Ready" : review.quality === "conflict" ? "Resolve conflict" : "Waiting for details"}</span></td>
          </tr>)}</tbody>
        </table></div>
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 text-sm text-ink-muted">
          <span>{data.total} reviews · Page {page} of {Math.max(1, Math.ceil(data.total / 50))}</span>
          <div className="flex gap-2"><button className="btn-secondary" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>Previous</button><button className="btn-secondary" disabled={page * 50 >= data.total} onClick={() => setPage(value => value + 1)}>Next</button></div>
        </footer>
      </div>}
  </div>;
}
