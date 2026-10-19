"use client";

import type { ReactNode, ReactElement } from "react";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { GoogleOAuthProvider, GoogleLogin } from "@react-oauth/google";
import { clearToken, readToken, writeToken } from "@/lib/auth-storage";
import { backendUrl } from "@/lib/api";

type UserPayload = {
  id: number; email: string; name: string; picture: string;
  account_tier: "standard" | "premium"; can_label_rows: boolean; is_admin: boolean;
};
type AuthCfg = { authRequired: boolean; googleClientId: string };
export type AuthStatus = "loading" | "disabled" | "signed-in" | "signed-out" | "unavailable";
type AuthContextValue = {
  status: AuthStatus; loading: boolean; cfg: AuthCfg; token: string | null; user: UserPayload | null;
  loginError: string | null; authError: string | null;
  setSession: (accessToken: string, user: UserPayload) => void;
  logout: () => void; retryAuth: () => void; LoginButton: () => ReactElement;
};
const AuthContext = createContext<AuthContextValue | null>(null);

export function safeNext(path: string | null): string {
  return path && path.startsWith("/") && !path.startsWith("//") && !path.includes("\\") &&
    !/[\u0000-\u001f]/.test(path) && !path.startsWith("/login") ? path : "/";
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth requires AppProviders");
  return value;
}

export function AppProviders({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [cfg, setCfg] = useState<AuthCfg>({ authRequired: true, googleClientId: "" });
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<UserPayload | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const retryAuth = useCallback(() => { setStatus("loading"); setAuthError(null); setAttempt(n => n + 1); }, []);
  const setSession = useCallback((accessToken: string, account: UserPayload) => {
    writeToken(accessToken); setToken(accessToken); setUser(account); setStatus("signed-in"); setAuthError(null);
  }, []);
  const logout = useCallback(() => {
    clearToken(); setToken(null); setUser(null); setStatus("signed-out"); router.replace("/login");
  }, [router]);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    let alive = true;
    async function bootstrap() {
      try {
        const response = await fetch(backendUrl("/api/auth/config"), { signal: controller.signal });
        if (!response.ok) throw new Error();
        const body = await response.json();
        if (typeof body.auth_required !== "boolean") throw new Error();
        if (!alive) return;
        setCfg({ authRequired: body.auth_required, googleClientId: body.google_client_id || "" });
        if (!body.auth_required) { setStatus("disabled"); setUser(null); return; }
        const stored = readToken();
        setToken(stored);
        if (!stored) { setStatus("signed-out"); return; }
        const accountResponse = await fetch(backendUrl("/api/auth/me"), {
          headers: { Authorization: "Bearer " + stored }, signal: controller.signal,
        });
        if (!alive) return;
        if (accountResponse.status === 401) { clearToken(); setToken(null); setUser(null); setStatus("signed-out"); return; }
        if (!accountResponse.ok) throw new Error();
        const account = await accountResponse.json();
        if (!account.user) throw new Error();
        if (alive) { setUser(account.user); setStatus("signed-in"); }
      } catch {
        if (alive) { setAuthError("We could not connect to the sign-in service. Please try again."); setStatus("unavailable"); }
      } finally { clearTimeout(timeout); }
    }
    void bootstrap();
    return () => { alive = false; controller.abort(); clearTimeout(timeout); };
  }, [attempt]);

  useEffect(() => {
    const handler = () => {
      clearToken(); setToken(null); setUser(null); setStatus("signed-out");
      router.replace("/login?next=" + encodeURIComponent(window.location.pathname));
    };
    window.addEventListener("customsai:unauthorized", handler);
    return () => window.removeEventListener("customsai:unauthorized", handler);
  }, [router]);

  const LoginButton = useCallback(() => {
    if (!cfg.googleClientId) return <p className="text-sm text-ink-muted">Sign-in is unavailable. Contact your administrator.</p>;
    return <div className="space-y-3">
      <GoogleLogin theme="outline" shape="rectangular" size="large" width="300" text="continue_with"
        onSuccess={async credentialResponse => {
          setLoginError(null);
          if (!credentialResponse.credential) { setLoginError("Sign-in was not completed. Please try again."); return; }
          try {
            const response = await fetch(backendUrl("/api/auth/google"), {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ credential: credentialResponse.credential }), signal: AbortSignal.timeout(15000),
            });
            if (!response.ok) throw new Error("We could not sign you in. Please try again.");
            const body = await response.json();
            setSession(body.access_token, body.user);
            router.replace(safeNext(new URL(window.location.href).searchParams.get("next")));
          } catch { setLoginError("We could not complete sign-in. Check your connection and try again."); }
        }} onError={() => setLoginError("Google sign-in was not completed. Please try again.")} />
      {loginError && <p role="alert" className="rounded-lg bg-danger/5 p-3 text-sm text-danger">{loginError}</p>}
    </div>;
  }, [cfg.googleClientId, loginError, router, setSession]);
  const value = useMemo(() => ({ status, loading: status === "loading", cfg, token, user, loginError, authError,
    setSession, logout, retryAuth, LoginButton }), [status, cfg, token, user, loginError, authError, setSession, logout, retryAuth, LoginButton]);
  const inner = <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
  return cfg.googleClientId ? <GoogleOAuthProvider clientId={cfg.googleClientId}>{inner}</GoogleOAuthProvider> : inner;
}
