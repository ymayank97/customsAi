"use client";

import type { ReactNode, ReactElement } from "react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { GoogleOAuthProvider, GoogleLogin } from "@react-oauth/google";

import { clearToken, readToken, writeToken } from "@/lib/auth-storage";
import { backendUrl } from "@/lib/api";

type UserPayload = {
  id: number;
  email: string;
  name: string;
  picture: string;
};

type AuthCfg = {
  authRequired: boolean;
  googleClientId: string;
};

export type AuthStatus =
  | "loading"
  | "disabled"
  | "signed-in"
  | "signed-out";

type AuthContextValue = {
  status: AuthStatus;
  loading: boolean;
  cfg: AuthCfg;
  token: string | null;
  user: UserPayload | null;
  loginError: string | null;
  setSession: (accessToken: string, user: UserPayload) => void;
  logout: () => void;
  LoginButton: () => ReactElement;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth requires AppProviders");
  return v;
}

export function AppProviders({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [cfgLoaded, setCfgLoaded] = useState(false);
  const [meChecked, setMeChecked] = useState(false);
  const [cfg, setCfg] = useState<AuthCfg>({
    authRequired: false,
    googleClientId: "",
  });
  const [token, setToken] = useState<string | null>(() => readToken());
  const [user, setUser] = useState<UserPayload | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);

  const setSession = useCallback((accessToken: string, u: UserPayload) => {
    writeToken(accessToken);
    setToken(accessToken);
    setUser(u);
    setMeChecked(true);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setToken(null);
    setUser(null);
    router.replace("/login");
  }, [router]);

  // Listen for soft 401 events dispatched by the API layer.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = () => {
      clearToken();
      setToken(null);
      setUser(null);
      const path = window.location.pathname;
      if (path !== "/" && path !== "/login") {
        router.replace(`/login?next=${encodeURIComponent(path)}`);
      }
    };
    window.addEventListener("customsai:unauthorized", handler);
    return () => window.removeEventListener("customsai:unauthorized", handler);
  }, [router]);

  useEffect(() => {
    let alive = true;
    fetch(backendUrl("/api/auth/config"))
      .then((r) => r.json())
      .then((j) => {
        if (!alive) return;
        setCfg({
          authRequired: Boolean(j.auth_required),
          googleClientId: (j.google_client_id || "") as string,
        });
      })
      .catch(() => {})
      .finally(() => alive && setCfgLoaded(true));

    return () => {
      alive = false;
    };
  }, []);

  // Validate token against /api/auth/me; clear if rejected.
  useEffect(() => {
    if (!cfgLoaded) return;
    if (!cfg.authRequired) {
      setMeChecked(true);
      return;
    }
    if (!token) {
      setMeChecked(true);
      return;
    }
    let alive = true;
    const h = new Headers();
    h.set("Authorization", `Bearer ${token}`);
    fetch(backendUrl("/api/auth/me"), { headers: h })
      .then((r) => {
        if (!alive) return;
        if (r.status === 401) {
          clearToken();
          setToken(null);
          setUser(null);
          return null;
        }
        return r.ok ? r.json() : null;
      })
      .then((body) => {
        if (!alive) return;
        if (body?.user) setUser(body.user as UserPayload);
      })
      .catch(() => {})
      .finally(() => alive && setMeChecked(true));
    return () => {
      alive = false;
    };
  }, [cfgLoaded, cfg.authRequired, token]);

  const loading = !cfgLoaded || !meChecked;

  const status: AuthStatus = !cfgLoaded || !meChecked
    ? "loading"
    : !cfg.authRequired
    ? "disabled"
    : token && user
    ? "signed-in"
    : token && !user
    ? "loading"
    : "signed-out";

  const LoginButton = useCallback(() => {
    if (!cfg.googleClientId) {
      return (
        <p className="text-sm text-warn">
          Missing <code className="text-xs">GOOGLE_OAUTH_CLIENT_ID</code> on the backend.
        </p>
      );
    }
    return (
      <div className="space-y-2">
        <GoogleLogin
          useOneTap
          theme="filled_blue"
          shape="pill"
          size="large"
          width="280"
          text="signin_with"
          onSuccess={async (cred) => {
            setLoginError(null);
            const credential = cred.credential;
            if (!credential) {
              setLoginError("Google did not return a credential. Please try again.");
              return;
            }
            try {
              const r = await fetch(backendUrl("/api/auth/google"), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ credential }),
              });
              if (!r.ok) {
                setLoginError(`Sign-in failed (${r.status}). Please try again.`);
                return;
              }
              const data = await r.json();
              setSession(data.access_token, data.user);
              const url = new URL(window.location.href);
              const next = url.searchParams.get("next") || "/upload";
              router.replace(next.startsWith("/") ? next : "/upload");
            } catch {
              setLoginError("Network error. Check your connection and retry.");
            }
          }}
          onError={() => setLoginError("Google sign-in error. Try again.")}
        />
        {loginError ? (
          <div role="alert" className="text-sm text-danger bg-danger/5 border border-danger/20 rounded-lg px-3 py-2">
            {loginError}
          </div>
        ) : null}
      </div>
    );
  }, [cfg.googleClientId, loginError, setSession, router]);

  const value = useMemo(
    () =>
      ({
        status,
        loading,
        cfg,
        token,
        user,
        loginError,
        setSession,
        logout,
        LoginButton,
      }) satisfies AuthContextValue,
    [status, loading, cfg, token, user, loginError, setSession, logout, LoginButton]
  );

  const inner = <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;

  if (cfg.googleClientId) {
    return <GoogleOAuthProvider clientId={cfg.googleClientId}>{inner}</GoogleOAuthProvider>;
  }
  return inner;
}
