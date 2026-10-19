"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/providers/AppProviders";

/**
 * Wrap any client page that should require sign-in when auth is enforced.
 * Renders a tasteful skeleton while the auth state bootstraps so the page
 * never "flashes" before redirecting.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { status, authError, retryAuth } = useAuth();

  useEffect(() => {
    if (status === "signed-out") {
      const next = pathname && pathname !== "/login" ? `?next=${encodeURIComponent(pathname)}` : "";
      router.replace(`/login${next}`);
    }
  }, [status, pathname, router]);

  if (status === "unavailable") {
    return <div className="container-narrow py-20"><div className="surface p-8" role="alert">
      <h1 className="text-xl font-semibold">Unable to connect</h1>
      <p className="mt-2 text-ink-soft">{authError}</p>
      <button className="btn-primary mt-5" onClick={retryAuth}>Try again</button>
    </div></div>;
  }
  if (status === "loading" || status === "signed-out") {
    return (
      <div className="container-narrow pt-20 pb-24" aria-hidden>
        <div className="space-y-3">
          <div className="skeleton h-7 w-48 rounded-md" />
          <div className="skeleton h-4 w-72 rounded-md" />
          <div className="mt-8 skeleton h-48 rounded-2xl" />
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
