const TOKEN_KEY = "hotel-ai-staff-token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session lasts until reload */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function detailMessage(data: any, fallback: string): string {
  const d = data?.detail;
  if (!d) return fallback;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ");
  return JSON.stringify(d);
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; auth?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.auth) {
    const t = getToken();
    if (t) headers.Authorization = `Bearer ${t}`;
  }
  const res = await fetch(`/api${path}`, {
    method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    if (res.status === 401 && opts.auth) {
      setToken(null);
      window.dispatchEvent(new Event("staff-logout"));
    }
    throw new ApiError(res.status, detailMessage(data, `Lỗi ${res.status}`));
  }
  return data as T;
}

/** Staff API shorthand. */
export const sapi = {
  get: <T = any>(p: string) => api<T>(p, { auth: true }),
  post: <T = any>(p: string, body: unknown = {}) => api<T>(p, { auth: true, method: "POST", body }),
  put: <T = any>(p: string, body: unknown) => api<T>(p, { auth: true, method: "PUT", body }),
  patch: <T = any>(p: string, body: unknown) => api<T>(p, { auth: true, method: "PATCH", body }),
  del: <T = any>(p: string) => api<T>(p, { auth: true, method: "DELETE" }),
};
