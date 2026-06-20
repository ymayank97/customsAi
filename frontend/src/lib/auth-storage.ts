/** Session token for Bearer auth (survives page refresh until tab close). */
export const CUSTOMSAI_TOKEN_KEY = "customsai_access_token";

export function readToken(): string | null {
  if (typeof sessionStorage === "undefined") return null;
  try {
    return sessionStorage.getItem(CUSTOMSAI_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function writeToken(token: string) {
  sessionStorage.setItem(CUSTOMSAI_TOKEN_KEY, token);
}

export function clearToken() {
  sessionStorage.removeItem(CUSTOMSAI_TOKEN_KEY);
}
