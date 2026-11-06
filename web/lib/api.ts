/* The console's one way to talk to the backend. Every button goes through the same typed
   tools and role checks the assistant uses (POST /api/tools/{name}), so a screen can never do
   what its role could not ask for. */

import type { Role } from "./roles";

export type Config = {
  auth: "demo";
  stage?: string;
  /** Whether the server has Bedrock credentials (off: answers come from templates). */
  models?: boolean;
  /** Whether the server has PayPal sandbox credentials (off: payments are simulated). */
  paypal?: boolean;
  /** Whether signing in needs the shared demo password. */
  password?: boolean;
  /** The PayPal app's public client id, for the PayPal button in the browser. */
  paypal_client_id?: string | null;
  currency?: string;
};

export type DemoAccount = { user: string; role: Role; crew_id: string | null; label: string };

const IDENTITY = "crewpay.demo";
const SESSION = "crewpay.session";

export const saved = {
  get(k: string): string | null {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set(k: string, v: string) {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* private window */
    }
  },
  del(k: string) {
    try {
      localStorage.removeItem(k);
    } catch {
      /* private window */
    }
  },
};

/** Where the API lives. Empty when the console is served by the API itself; the API's own
 *  address when the console is hosted apart from it, set at build time. */
export const API_BASE = (process.env.NEXT_PUBLIC_CREWPAY_API ?? "").replace(/\/+$/, "");

/** The full address of an API path such as /api/me. */
export const apiUrl = (path: string) => `${API_BASE}${path}`;

let config: Config | null = null;
let configLoading: Promise<Config> | null = null;

export function loadConfig(): Promise<Config> {
  if (config) return Promise.resolve(config);
  configLoading ??= fetch(apiUrl("/api/config"), { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : { auth: "demo" }))
    .catch(() => ({ auth: "demo" }))
    .then((c: Config) => (config = c));
  return configLoading;
}

export const authMode = () => "demo" as const;

export function demoIdentity(): DemoAccount | null {
  const raw = saved.get(IDENTITY);
  if (!raw) return null;
  try {
    const a = JSON.parse(raw) as DemoAccount;
    return a && a.user && a.role ? a : null;
  } catch {
    return null;
  }
}
export function setDemoIdentity(a: DemoAccount, token?: string | null) {
  saved.set(IDENTITY, JSON.stringify(a));
  if (token) saved.set(SESSION, token);
  else saved.del(SESSION);
}

/** Sign in as a demo account, with the shared password when the server asks for one. */
export async function signIn(a: DemoAccount, password: string): Promise<void> {
  const r = await api<{ token: string | null }>("/api/login", { method: "POST", json: { user: a.user, password }, auth: false });
  setDemoIdentity(a, r.token);
}

/** Signed in, as far as this browser knows: a chosen demo identity. */
export function hasSession(): boolean {
  return Boolean(demoIdentity());
}

export function signOut() {
  saved.del(IDENTITY);
  saved.del(SESSION);
}

export function headers(): Record<string, string> {
  const h: Record<string, string> = {};
  const token = saved.get(SESSION);
  if (token) {
    h["Authorization"] = `Bearer ${token}`;
    return h;
  }
  const id = demoIdentity();
  if (id) {
    h["X-Demo-User"] = id.user;
    h["X-Demo-Role"] = id.role;
    if (id.crew_id) h["X-Demo-Crew-Id"] = id.crew_id;
  }
  return h;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

function detail(body: unknown, status: number): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => (x as { msg?: string }).msg ?? String(x)).join("; ");
  if (status === 404) return "Not available on this server yet.";
  return `Request failed (${status}).`;
}

export function toSignIn() {
  const here = window.location.pathname + window.location.search;
  window.location.assign(`/login/?next=${encodeURIComponent(here)}`);
}

export async function api<T = unknown>(
  path: string,
  opts: { method?: string; json?: unknown; form?: FormData; auth?: boolean } = {},
): Promise<T> {
  await loadConfig();
  const init: RequestInit = { method: opts.method ?? "GET", headers: opts.auth === false ? {} : headers(), cache: "no-store" };
  if (opts.json !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.json);
  } else if (opts.form) {
    init.body = opts.form;
  }
  let res: Response;
  try {
    res = await fetch(apiUrl(path), init);
  } catch {
    throw new ApiError("The CrewPay server cannot be reached.", 0);
  }
  const body = await res.json().catch(() => null);
  if (res.status === 401 && opts.auth !== false && typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    signOut();
    toSignIn();
  }
  if (!res.ok) throw new ApiError(detail(body, res.status), res.status);
  return body as T;
}

/** A tool call. Tools answer problems as `{"error": ...}` with HTTP 200; that becomes a thrown
 * error here, so callers show it in place. */
export async function tool<T = Record<string, unknown>>(name: string, args: Record<string, unknown> = {}): Promise<T> {
  const r = await api<T & { error?: string }>(`/api/tools/${name}`, { method: "POST", json: args });
  if (r && typeof r === "object" && "error" in r && r.error) throw new ApiError(String(r.error), 200);
  return r;
}

/** True when the error says the server does not have this tool or endpoint yet. */
export function missing(err: unknown): boolean {
  const m = err instanceof Error ? err.message : String(err);
  return /no tool named|not available on this server|\(404\)/i.test(m);
}

export const message = (err: unknown) => (err instanceof Error ? err.message : String(err));
