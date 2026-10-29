"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { LogOut } from "lucide-react";
import { api, type HealthResponse } from "@/lib/api";
import { useAuth } from "@/providers/AppProviders";
import { cn } from "@/lib/utils";

export function TopBar() {
  const pathname = usePathname();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthErr, setHealthErr] = useState(false);
  const [healthStarting, setHealthStarting] = useState(false);
  const { status, cfg, logout, user } = useAuth();

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const h = await api.health();
        if (!alive) return;
        setHealth(h);
        setHealthStarting(false);
        setHealthErr(false);
      } catch (e: any) {
        if (!alive) return;
        const msg = String(e?.message || "");
        if (msg.includes("503") || msg.toLowerCase().includes("starting")) {
          setHealthStarting(true);
          setHealthErr(false);
          timer = setTimeout(tick, 4000);
        } else {
          setHealthErr(true);
          setHealthStarting(false);
        }
      }
    };
    void tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, []);

  const dotCls = healthErr
    ? "bg-danger"
    : healthStarting
    ? "bg-warn animate-pulse"
    : health
    ? "bg-success"
    : "bg-line2";

  const statusLabel = healthErr
    ? "Offline"
    : healthStarting
    ? "Warming up"
    : health
    ? health.classification_available ? "Ready" : health.enriched_master ? "Training required" : "Tariff data required"
    : "";

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-page/85 backdrop-blur-md">
      <div className="container-wide flex h-14 items-center justify-between gap-4">
        <Link href="/" className="flex items-center gap-2.5 group">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-ink text-white text-[11px] font-bold tracking-tight shadow-soft transition-transform group-hover:scale-105">
            cA
          </span>
          <span className="flex items-baseline gap-2">
            <span className="text-[15px] font-semibold tracking-tight">customsAI</span>
            <span className="hidden sm:inline text-xs text-ink-muted">CTH fraud audit</span>
          </span>
        </Link>

        <nav className="hidden md:flex items-center gap-1 text-sm">
          <NavLink href="/" active={pathname === "/"}>Home</NavLink>
          <NavLink href="/upload" active={pathname?.startsWith("/upload") ?? false}>Upload</NavLink>
          <NavLink href="/jobs" active={pathname?.startsWith("/jobs") ?? false}>Jobs</NavLink>
        </nav>

        <div className="flex items-center gap-3">
          {statusLabel ? (
            <span
              className={cn(
                "hidden sm:inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium",
                healthErr ? "text-danger" : "text-ink-muted"
              )}
              title={healthErr ? "Backend unreachable" : "Connected to the audit service"}
            >
              <span className={cn("h-1.5 w-1.5 rounded-full transition-colors", dotCls)} />
              {statusLabel}
            </span>
          ) : null}

          {status === "loading" ? (
            <div className="h-8 w-8 rounded-full bg-line/60 animate-pulse" />
          ) : status === "signed-in" ? (
            <UserMenu
              email={user?.email ?? ""}
              name={user?.name ?? ""}
              picture={user?.picture ?? ""}
              onSignOut={logout}
            />
          ) : status === "signed-out" && pathname !== "/login" ? (
            <Link
              href="/login"
              className="text-xs font-medium px-3 py-1.5 rounded-full bg-ink text-white shadow-soft hover:opacity-90 transition"
            >
              Sign in
            </Link>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function NavLink({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className={cn(
        "rounded-md px-2.5 py-1.5 transition-colors",
        active ? "text-ink bg-page" : "text-ink-muted hover:text-ink hover:bg-page"
      )}
    >
      {children}
    </Link>
  );
}

function UserMenu({
  email,
  name,
  picture,
  onSignOut,
}: {
  email: string;
  name: string;
  picture: string;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const initials =
    (name || email).split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((s) => s[0]?.toUpperCase()).join("") || "·";

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cn(
          "h-8 w-8 rounded-full overflow-hidden border border-line bg-surface grid place-items-center",
          "transition hover:shadow-soft focus:outline-none focus:ring-2 focus:ring-accent/30"
        )}
        title={email}
      >
        {picture ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={picture} alt="" className="h-full w-full object-cover" referrerPolicy="no-referrer" />
        ) : (
          <span className="text-[11px] font-semibold text-ink-soft">{initials}</span>
        )}
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 mt-2 w-64 origin-top-right rounded-xl border border-line bg-surface shadow-lift overflow-hidden animate-fade-in"
        >
          <div className="px-3.5 py-3 border-b border-line">
            <div className="text-[13px] font-medium truncate">{name || email.split("@")[0]}</div>
            <div className="text-[11px] text-ink-muted truncate">{email}</div>
          </div>
          <button
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onSignOut();
            }}
            className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13px] text-ink-soft hover:bg-page transition"
          >
            <LogOut className="h-3.5 w-3.5" />
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
