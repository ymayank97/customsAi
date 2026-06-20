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
  const { status } = useAuth();

  useEffect(() => {
    if (status === "signed-out") {
      const next = pathname && pathname !== "/login" ? `?next=${encodeURIComponent(pathname)}` : "";
      router.replace(`/login${next}`);
    }
  }, [status, pathname, router]);

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
