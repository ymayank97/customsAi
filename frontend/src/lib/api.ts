// Browser calls the backend directly (CORS). Server-side uses the same base when unset.
import { readToken, clearToken } from "./auth-storage";

function apiBase(): string {
  const env = typeof process !== "undefined" ? process.env.NEXT_PUBLIC_BACKEND_URL?.trim() : "";
  if (env) return env.replace(/\/$/, "");
  return "http://127.0.0.1:8000";
}

function apiUrl(path: string): string {
  const base = apiBase();
  if (!path.startsWith("/")) return `${base}/${path}`;
  if (!base) return path;
  return `${base}${path}`;
}

export function backendUrl(path: string): string {
  return apiUrl(path);
}

async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const h = new Headers(init?.headers as HeadersInit);
  const t = readToken();
  if (t) h.set("Authorization", `Bearer ${t}`);
  const url = input.startsWith("http") ? input : apiUrl(input);
  return fetch(url, { ...init, headers: h });
}

function redirectUnauthorized(): void {
  if (typeof window === "undefined") return;
  clearToken();
  window.dispatchEvent(new CustomEvent("customsai:unauthorized"));
}

async function asJson<T>(r: Response): Promise<T> {
  if (r.status === 401) {
    redirectUnauthorized();
    throw new Error("Session expired");
  }
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((issue: { msg: string }) => issue.msg).join(". ") : null;
    throw new Error(message || `Request failed (${r.status}). Please try again.`);
  }
  return (await r.json()) as T;
}

export type JobStatus = "queued" | "running" | "done" | "failed";

export interface JobSummary {
  id: string;
  filename: string;
  status: JobStatus;
  rows_total: number;
  rows_processed: number;
  started_at: number;
  completed_at: number;
}

export interface JobDetail extends JobSummary {
  progress: {
    phase: string;
    completed: number;
    total: number;
    eta_seconds?: number | null;
  };
  error: string;
  columns_detected: Record<string, string>;
  eta_seconds?: number | null;
}

export interface AuditStage {
  stage: string;
  explanation?: string;
  similarity?: number;
  top_chapters?: { chapter: string; prob: number }[];
  top_candidates?: {
    cth: string;
    score: number;
    chapter?: string;
    description?: string;
    source?: string;
  }[];
  proposed_cth?: string;
  proposed_bcd?: number | null;
  confidence?: number | null;
  reasoning?: string;
  llm_failed?: boolean;
  cth_final?: string;
  bcd_final?: number | null;
  hallucination?: boolean;
  override_reason?: string;
  declared_cth?: string;
  declared_bcd?: number | null;
  expected_bcd?: number | null;
  bcd_source?: string;
  declared_notification?: string;
  matched_notification?: string;
  candidate_chapters?: string[];
  docket?: LegalDocketItem[];
  pruned?: Array<{ cth?: string; rule_id?: string; citation?: string }>;
  conformal_set?: string[];
  gir_path?: string;
  citations?: string[];
  precedents?: PrecedentCitation[];
  manual_queue?: boolean;
  bcd_basis?: string | null;
  revenue_basis?: RevenueBasis;
}

export interface AuditTrace {
  summary?: string;
  stages: AuditStage[];
}

export type ReviewDecision = "confirmed" | "corrected" | "needs_information";

export interface ReviewPayload {
  job_id: string;
  row_idx: number;
  decision: ReviewDecision;
  corrected_cth?: string | null;
  corrected_bcd?: number | null;
  reference: string;
  reasoning: string;
  product_details: string;
}

export interface TrainingLabel extends ReviewPayload {
  id: number;
  user_id: number;
  item_description: string;
  declared_cth: string;
  predicted_cth: string;
  predicted_bcd: number | null;
  reviewer_name: string;
  created_at: string;
  updated_at: string;
  quality?: "ready" | "conflict" | "needs_information";
}

export interface ReviewList {
  items: TrainingLabel[];
  total: number;
  scope: "my_reviews" | "all_reviewers";
  summary: { reviewed: number; training_examples: number; needs_information: number; conflicts: number };
}

export interface Candidate {
  cth: string;
  description: string;
  chapter_no?: number;
  chapter_title?: string;
  chapter_notes?: string;
  unit?: string;
  import_policy?: string;
  export_policy?: string;
  bcd_statutory: number | null;
  bcd_effective: number | null;
  bcd_sch?: string;
  bcd_ntfn?: string;
  bcd_notifications_applied?: string[];
  applicable_notifications?: string[];
  notification_details?: NotificationDetail[];
  bcd_breakdown?: BcdBreakdown;
  retrieval_score?: number;
  retrieval_margin?: number | null;
  retrieval_ambiguous?: boolean;
  source?: string;
}

export interface NotificationDetail {
  id: string;
  title?: string;
  date?: string;
  type?: string;
  effective_date?: string;
}

export interface BcdBreakdown {
  statutory?: number | null;
  effective?: number | null;
  schedule_text?: string;
  notification_text?: string;
  notification_source?: string[];
}

export interface RateResolution {
  bcd_audit?: number | null;
  bcd_basis?: string | null;
  misuse_suspected?: boolean;
  detail?: string | null;
  matched_variant?: Record<string, unknown> | null;
  condition_scores?: Array<Record<string, unknown>>;
}

export interface RetrievalDiagnostics {
  margin?: number | null;
  ambiguous?: boolean;
}

export interface ChapterInfo {
  no: string;
  title: string;
}

export interface ClassificationBreakdown {
  chapter: string;
  chapter_title?: string;
  heading: string;
  heading_text?: string;
  subheading_6: string;
  subheading_text?: string;
  leaf_text?: string;
  discriminator: string;
  is_residuary: boolean;
  attributes?: Record<string, unknown>;
}

export interface LegalDocketItem {
  rule_id?: string;
  citation?: string;
  kind?: string;
  bears_on?: string[];
  cth?: string;
}

export interface PrecedentCitation {
  ruling_id?: string;
  title?: string;
  authority?: string;
  outcome_cth?: string;
  competing_pair?: string;
  legal_basis?: string;
  source_url?: string;
  score?: number;
  heading_overlap?: number;
}

export interface RevenueBasis {
  valid: boolean;
  basis?: string;
  detail?: string;
}

export interface RowResult {
  review?: TrainingLabel | null;
  row_idx: number;
  declared_cth: string;
  declared_bcd: number | null;
  declared_notification?: string;
  item_description: string;
  importer?: string;
  be_no?: string;
  assess_val?: number | null;
  final: {
    cth_final: string;
    bcd_final: number | null;
    tier_used:
      | "stage3_adjudicated"
      | "stage4_validated"
      | "stage1_fallback"
      | "manual_queue";
    confidence: number;
    reasoning: string;
    candidates: Candidate[];
    disagreement: string;
    breakdown?: ClassificationBreakdown;
    conformal_set?: string[];
    citations?: string[];
    gir_path?: string;
    legal_docket?: LegalDocketItem[];
    legal_summary?: string;
    precedent_citations?: PrecedentCitation[];
    revenue_basis?: RevenueBasis;
    manual_queue?: boolean;
  };
  fraud: {
    is_fraud: boolean;
    flag_type:
      | ""
      | "CTH Mismatch"
      | "BCD Under-declared"
      | "BCD Over-declared"
      | "Both"
      | "Notification misuse";
    revenue_at_risk: number | null;
    detail: string;
  };
  review_flags?: string[];
  rate_resolution?: RateResolution;
  retrieval_diagnostics?: RetrievalDiagnostics;
  declared_chapter?: ChapterInfo;
  recommended_chapter?: ChapterInfo;
  chapter_changed?: boolean;
  unit?: string;
  import_policy?: string;
  export_policy?: string;
  bcd_breakdown?: BcdBreakdown;
  applicable_notifications?: string[];
  notification_details?: NotificationDetail[];
  hallucination?: boolean;
  audit_trace?: AuditTrace;
}

export interface ResultsPage {
  total: number;
  page: number;
  page_size: number;
  status?: JobStatus;
  rows_processed?: number;
  rows_total?: number;
  rows: RowResult[];
}

export interface HealthResponse {
  status: string;
  enriched_master: boolean;
  llm_provider: string;
  has_anthropic_key: boolean;
  has_google_key: boolean;
  auth_enforced?: boolean;
}

export const api = {
  health: (): Promise<HealthResponse> =>
    apiFetch("/api/health").then((r) => asJson<HealthResponse>(r)),

  uploadFile: async (
    file: File
  ): Promise<{ job_id: string; status: JobStatus }> => {
    const fd = new FormData();
    fd.append("file", file);
    const r = await apiFetch("/api/uploads", { method: "POST", body: fd });
    return asJson<{ job_id: string; status: JobStatus }>(r);
  },

  saveReview: async (payload: ReviewPayload) => {
    const r = await apiFetch("/api/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return asJson<{ ok: boolean; review: TrainingLabel }>(r);
  },

  listReviews: (opts: { q?: string; page?: number } = {}) =>
    apiFetch(`/api/reviews?${new URLSearchParams({ q: opts.q || "", page: String(opts.page || 1) })}`)
      .then((r) => asJson<ReviewList>(r)),

  tariffCode: (code: string) => apiFetch(`/api/cth/${encodeURIComponent(code)}`)
    .then((r) => asJson<{ description?: string; leaf_text?: string }>(r)),

  downloadTrainingLabels: async () => {
    const r = await apiFetch("/api/reviews/export");
    if (!r.ok) await asJson(r);
    const url = URL.createObjectURL(await r.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "reviewed_training_labels.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  },

  listJobs: (): Promise<JobSummary[]> =>
    apiFetch("/api/uploads").then((r) => asJson<JobSummary[]>(r)),

  jobStatus: (id: string): Promise<JobDetail> =>
    apiFetch(`/api/uploads/${id}`).then((r) => asJson<JobDetail>(r)),

  results: (
    id: string,
    opts?: { page?: number; page_size?: number; fraud_only?: boolean }
  ): Promise<ResultsPage> => {
    const params = new URLSearchParams({
      page: String(opts?.page ?? 1),
      page_size: String(opts?.page_size ?? 10000),
      fraud_only: String(opts?.fraud_only ?? false),
    });
    return apiFetch(`/api/uploads/${id}/results?${params}`).then((r) =>
      asJson<ResultsPage>(r)
    );
  },

  downloadUrl: (id: string) => apiUrl(`/api/uploads/${id}/download`),

  downloadAnnotated: async (
    jobId: string,
    fallbackName?: string
  ): Promise<void> => {
    const r = await apiFetch(`/api/uploads/${jobId}/download`);
    if (r.status === 401) {
      redirectUnauthorized();
      throw new Error("Session expired");
    }
    if (!r.ok) {
      const text = await r.text().catch(() => "");
      throw new Error(text || `${r.status} ${r.statusText}`);
    }
    const blob = await r.blob();
    const safe =
      fallbackName?.replace(/\s+/g, "_").replace(/[^a-z0-9._-]/gi, "") ||
      `audit_${jobId}`;
    const fn = safe.endsWith(".xlsx") ? safe : `${safe}.xlsx`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fn;
    a.click();
    URL.revokeObjectURL(url);
  },

  reevaluate: (payload: {
    item_description: string;
    declared_cth?: string;
    declared_bcd?: number | null;
    row_idx?: number;
    top_k?: number;
    strict?: boolean;
  }): Promise<{
    item_description: string;
    candidates: Candidate[];
    classification?: {
      cth_final?: string;
      bcd_final?: number | null;
      confidence?: number;
      reasoning?: string;
      tier_used?: string;
      disagreement?: string;
    };
  }> =>
    apiFetch("/api/classify/reevaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) =>
      asJson<{
        item_description: string;
        candidates: Candidate[];
        classification?: {
          cth_final?: string;
          bcd_final?: number | null;
          confidence?: number;
          reasoning?: string;
          tier_used?: string;
          disagreement?: string;
        };
      }>(r)
    ),

  // SSE chat — returns an EventSource-like reader. Pass `signal` (from AbortController) to cancel.
  chatStream: async function* (
    message: string,
    context?: { row_idx?: number; cth?: string; description?: string },
    signal?: AbortSignal
  ): AsyncGenerator<{ type: "delta" | "candidates" | "done" | "error"; data: any }, void, unknown> {
    const r = await apiFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, context, stream: true }),
      signal,
    });
    if (r.status === 401) {
      redirectUnauthorized();
      throw new Error("Session expired");
    }
    if (!r.ok || !r.body) {
      throw new Error(`Chat failed: ${r.status} ${r.statusText}`);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      while (true) {
        if (signal?.aborted) {
          try { await reader.cancel(); } catch {}
          break;
        }
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          let event = "delta";
          let dataLine = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLine += line.slice(5).trimStart();
          }
          if (!dataLine) continue;
          try {
            yield { type: event as any, data: JSON.parse(dataLine) };
          } catch {
            yield { type: event as any, data: dataLine };
          }
        }
      }
    } finally {
      try { await reader.cancel(); } catch {}
    }
  },
};
