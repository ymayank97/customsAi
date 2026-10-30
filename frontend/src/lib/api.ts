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
    const text = await r.text().catch(() => "");
    throw new Error(text || `${r.status} ${r.statusText}`);
  }
  return (await r.json()) as T;
}

export type JobStatus = "queued" | "running" | "done" | "failed";
export interface JobSummary {
  id: string; filename: string; status: JobStatus; rows_total: number;
  rows_processed: number; started_at: number; completed_at: number;
}
export interface JobDetail extends JobSummary {
  progress: { phase: string; completed: number; total: number; eta_seconds?: number | null };
  error: string; columns_detected: Record<string, string>; eta_seconds?: number | null;
}
export interface Candidate {
  cth: string; description: string; score: number; bcd_statutory?: number | null;
}
export interface RowResult {
  row_idx: number; item_description: string; declared_cth: string; declared_bcd: number | null;
  final: {
    cth_final: string; bcd_final: number | null; confidence: number; reasoning: string;
    tier_used: "hierarchical_model" | "manual_queue"; candidates: Candidate[];
    supported_prefix: string; manual_queue: boolean;
    suggested_cth?: string; training_source?: string;
    acceptance_basis?: string; prediction_level?: string;
  };
  fraud: { is_fraud: boolean; flag_type: string; revenue_at_risk: number | null; detail: string };
  review_flags: string[];
  bcd_assessment?: {
    score: number | null;
    status: "match" | "under_declared" | "over_declared" | "unverified";
    provided_rate: number | null; reference_rate: number | null;
    difference_pp: number | null; verified: boolean; requires_review: boolean;
    fraud_review_priority: "High" | "Medium" | "Low" | "Review required";
    potential_underpayment: boolean | null; reason: string;
  };
  audit_trace: { summary: string; stages: { stage: string; explanation: string }[] };
}
export interface ResultsPage {
  total: number; page: number; page_size: number; rows: RowResult[];
  summary: { total: number; classified: number; suggested: number; review: number; findings: number; revenue_at_risk: number };
}
export interface HealthResponse {
  status: string; enriched_master: boolean; model_trained: boolean;
  classification_available: boolean; auth_enforced: boolean;
  training_source?: string | null;
}
export interface FeedbackPayload {
  job_id?: string; row_idx: number; item_description: string; declared_cth?: string;
  predicted_cth?: string; predicted_bcd?: number | null; corrected_cth: string;
  corrected_bcd?: number | null; reasoning?: string;
}

export const api = {
  reclassify: (id: string): Promise<{ job_id: string; status: JobStatus }> =>
    apiFetch(`/api/uploads/${id}/reclassify`, { method: "POST" }).then(r => asJson(r)),
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

  saveFeedback: async (payload: FeedbackPayload) => {
    const r = await apiFetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return asJson<{ ok: boolean; feedback: any }>(r);
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
      page_size: String(opts?.page_size ?? 50),
      fraud_only: String(opts?.fraud_only ?? false),
    });
    return apiFetch(`/api/uploads/${id}/results?${params}`).then((r) =>
      asJson<ResultsPage>(r)
    );
  },

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

};
