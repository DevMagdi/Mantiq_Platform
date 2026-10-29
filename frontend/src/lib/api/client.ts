/**
 * Typed fetch client. Response/request bodies come from the generated OpenAPI schema only —
 * handwritten DTOs are forbidden (spec §3).
 */
import type { components, paths } from "./schema";

export type ApiSchemas = components["schemas"];
/** The uniform `{code, message, details}` envelope, generated from the OpenAPI error model. */
export type ApiError = components["schemas"]["ErrorResponse"];

const BASE = "/api/v1";

export class ApiError_ extends Error {
  code: string;
  status: number;
  details?: unknown;
  constructor(status: number, payload: Partial<ApiError> | null, fallbackMessage: string) {
    super(payload?.message ?? fallbackMessage);
    this.code = payload?.code ?? (status === 401 ? "unauthorized" : "error");
    this.status = status;
    this.details = payload?.details;
  }
  get isUnauthenticated() {
    return this.status === 401;
  }
  get isNotFound() {
    return this.status === 404;
  }
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();
export function onUnauthorized(listener: Listener): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

let accessToken: string | null = null;
export function setAccessToken(token: string | null) {
  accessToken = token;
}
export function getAccessToken() {
  return accessToken;
}

type Options = Omit<RequestInit, "body"> & { body?: unknown; query?: Record<string, unknown> };

function qs(query?: Record<string, unknown>): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.set(key, String(value));
    }
  }
  const rendered = params.toString();
  return rendered ? `?${rendered}` : "";
}

export async function request<T>(path: keyof paths | string, options: Options = {}): Promise<T> {
  const { query, body, headers, ...rest } = options;
  const isForm = body instanceof FormData;
  const response = await fetch(`${BASE}${path}${qs(query)}`, {
    credentials: "include",
    ...rest,
    headers: {
      ...(isForm ? {} : { "content-type": "application/json" }),
      ...(accessToken ? { authorization: `Bearer ${accessToken}` } : {}),
      ...(headers ?? {}),
    },
    body: isForm ? (body as FormData) : body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const payload: unknown = text ? safeParse(text) : null;
  if (!response.ok) {
    if (response.status === 401) unauthorizedListeners.forEach((fn) => fn());
    throw new ApiError_(response.status, payload as Partial<ApiError> | null, `Request failed (${response.status})`);
  }
  return payload as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { code: "invalid_response", message: text.slice(0, 200) };
  }
}

export const api = {
  get: <T,>(path: keyof paths | string, query?: Record<string, unknown>) =>
    request<T>(path, { method: "GET", query }),
  post: <T,>(path: keyof paths | string, body?: unknown, query?: Record<string, unknown>) =>
    request<T>(path, { method: "POST", body, query }),
  patch: <T,>(path: keyof paths | string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  put: <T,>(path: keyof paths | string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  del: <T,>(path: keyof paths | string) => request<T>(path, { method: "DELETE" }),
  upload: <T,>(path: keyof paths | string, form: FormData) =>
    request<T>(path, { method: "POST", body: form }),
};

/**
 * `POST /leads` refuses a duplicate mobile with 409 + a typed payload (spec §9.10).
 * Own-pipeline duplicates carry the lead reference; other reps' leads are never named (spec §6).
 */
export function duplicateInfo(error: unknown): { leadId: string | null; name: string | null; hidden: boolean; message: string } {
  if (!(error instanceof ApiError_)) return { leadId: null, name: null, hidden: false, message: String(error) };
  const details = (error.details ?? {}) as Record<string, unknown>;
  return {
    leadId: typeof details.lead_id === "string" ? details.lead_id : null,
    name: typeof details.name === "string" ? details.name : null,
    hidden: details.duplicate_hidden === true,
    message: error.message,
  };
}
