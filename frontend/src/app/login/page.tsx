"use client";

import { Suspense, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { BookOpen, Check, ClipboardCheck, ShieldCheck } from "lucide-react";
import { safeNext, useAuth } from "@/providers/AppProviders";

export default function LoginPage() {
  return <Suspense fallback={<div className="container-narrow py-20">Loading sign-in…</div>}><Login /></Suspense>;
}

function Login() {
  const { status, LoginButton, authError, retryAuth } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  useEffect(() => {
    if (status === "signed-in" || status === "disabled") router.replace(safeNext(params.get("next")));
  }, [status, params, router]);
  return <div className="container-wide py-10 sm:py-20">
    <div className="surface grid overflow-hidden lg:grid-cols-[1.1fr_1fr]">
      <section className="bg-accent-soft/40 p-7 sm:p-12">
        <span className="inline-flex items-center gap-2 text-sm font-semibold text-accent"><ShieldCheck className="h-5 w-5" /> Customs review workspace</span>
        <h1 className="mt-6 font-serif text-4xl sm:text-5xl leading-tight">Review Bills of Entry<br />with a clearer picture.</h1>
        <p className="mt-5 max-w-md text-base leading-relaxed text-ink-soft">Compare declared tariff codes with suggestions, inspect potential duty differences, and record your decision.</p>
        <ol className="mt-8 space-y-5">
          {[
            ["Upload your Excel file", "Start with a Bill of Entry in .xlsx format."],
            ["Focus on items needing attention", "See the description, tariff code and duty difference together."],
            ["Record an officer review", "Approved premium reviewers save verified decisions for future model training."],
          ].map(([title, detail], index) => <li key={title} className="flex gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-accent/20 bg-surface text-sm font-semibold text-accent">{index + 1}</span>
            <div><p className="font-medium">{title}</p><p className="mt-1 text-sm leading-relaxed text-ink-soft">{detail}</p></div>
          </li>)}
        </ol>
      </section>
      <section className="p-7 sm:p-12">
        <h2 className="text-2xl font-semibold tracking-tight">Sign in to your workspace</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">Use your Google account. Access is based on the account approved by your administrator.</p>
        <div className="mt-7 space-y-3">
          <AccessCard icon={<BookOpen className="h-5 w-5" />} title="Standard access" features={["Upload and view your audits", "Read tariff suggestions and download reports"]} />
          <AccessCard icon={<ClipboardCheck className="h-5 w-5" />} title="Premium reviewer" premium features={["Everything in Standard", "Confirm or correct individual items", "Save reviewed items and export training labels"]} />
        </div>
        <div className="mt-8 min-h-12">
          {status === "loading" ? <p role="status" className="text-sm text-ink-muted">Connecting to sign-in…</p> :
            status === "unavailable" ? <div role="alert"><p className="text-sm text-danger">{authError}</p><button onClick={retryAuth} className="btn-secondary mt-3">Try again</button></div> :
            status === "signed-out" ? <LoginButton /> : <p role="status" className="text-sm text-ink-muted">Opening your workspace…</p>}
        </div>
        <p className="mt-6 border-t border-line pt-5 text-sm text-ink-muted">Need reviewer access? Ask your administrator to approve your email. Signing in does not change your access level.</p>
      </section>
    </div>
  </div>;
}

function AccessCard({ icon, title, features, premium = false }: { icon: React.ReactNode; title: string; features: string[]; premium?: boolean }) {
  return <div className={premium ? "rounded-xl border border-accent/25 bg-accent-soft/30 p-4" : "rounded-xl border border-line p-4"}>
    <div className="flex items-center gap-2 text-sm font-semibold">{icon}{title}{premium && <span className="ml-auto rounded-full bg-surface px-2 py-1 text-xs font-medium text-accent">Approval required</span>}</div>
    <ul className="mt-3 space-y-2">{features.map(feature => <li key={feature} className="flex gap-2 text-sm text-ink-soft"><Check className="mt-0.5 h-4 w-4 shrink-0 text-success" />{feature}</li>)}</ul>
  </div>;
}
