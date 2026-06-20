"use client";

import { Suspense, useEffect } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRight, Loader2 } from "lucide-react";
import { useAuth } from "@/providers/AppProviders";

export default function LoginPage() {
  return (
    <Suspense fallback={<LoginShell><LoadingState /></LoginShell>}>
      <LoginInner />
    </Suspense>
  );
}

function LoginInner() {
  const router = useRouter();
  const search = useSearchParams();
  const { status, LoginButton } = useAuth();

  useEffect(() => {
    if (status === "signed-in" || status === "disabled") {
      const next = search.get("next") || "/upload";
      router.replace(next.startsWith("/") ? next : "/upload");
    }
  }, [status, router, search]);

  if (status === "loading" || status === "signed-in") {
    return (
      <LoginShell>
        <LoadingState />
      </LoginShell>
    );
  }

  if (status === "disabled") {
    return (
      <LoginShell>
        <h1 className="font-serif text-[32px] leading-tight tracking-tight">No sign-in needed</h1>
        <p className="mt-3 text-sm text-ink-muted">
          This deployment is running without OAuth — you can use it directly.
        </p>
        <div className="mt-7 flex justify-center">
          <Link href="/upload" className="btn-primary">
            Continue
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </LoginShell>
    );
  }

  return (
    <LoginShell>
      <Brand />
      <h1 className="mt-6 font-serif text-[36px] leading-[1.05] tracking-tight">
        Sign in to continue
      </h1>
      <p className="mt-3 text-[15px] leading-relaxed text-ink-soft">
        Use your authorized Google work account. A sign-in window will pop up.
      </p>
      <div className="mt-8 flex justify-center">
        <LoginButton />
      </div>
      <p className="mt-8 text-[12px] text-ink-muted">
        Bill of Entry files stay on this secure deployment.
      </p>
    </LoginShell>
  );
}

function LoginShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-[calc(100vh-3.5rem)] grid place-items-center px-6 pb-24">
      <div className="w-full max-w-[400px] animate-slide-up">
        <div className="rounded-3xl border border-line bg-surface shadow-lift p-10 text-center">
          {children}
        </div>
        <p className="mt-5 text-center text-[11px] text-ink-muted">
          <Link href="/" className="hover:text-ink transition">
            ← Back home
          </Link>
        </p>
      </div>
    </div>
  );
}

function Brand() {
  return (
    <div className="flex justify-center">
      <span className="grid h-11 w-11 place-items-center rounded-2xl bg-ink text-white text-[15px] font-bold tracking-tight shadow-soft">
        cA
      </span>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col items-center gap-4 py-6">
      <Loader2 className="h-5 w-5 animate-spin text-ink-muted" />
      <p className="text-sm text-ink-muted">Checking your session…</p>
    </div>
  );
}
