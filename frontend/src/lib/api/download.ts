/**
 * Binary responses (file downloads) from the API.
 *
 * `request()` in client.ts is JSON-only by contract, which is right for every screen but wrong for a
 * workbook: parsing it as JSON would corrupt it. This keeps the same credentials, the same access token
 * and the same `{code,message,details}` error envelope, and adds the plumbing a browser download needs
 * (filename from `Content-Disposition`, then a revoke-able object URL).
 */
import { ApiError_, type ApiError, getAccessToken } from "./client";

const BASE = "/api/v1";

export type DownloadedFile = {
  filename: string;
  blob: Blob;
  rows: number | null;
  truncated: boolean;
};

export async function downloadFile(path: string, body?: unknown): Promise<DownloadedFile> {
  return send(path, body === undefined ? undefined : JSON.stringify(body));
}

/**
 * A GET download — an issued policy PDF, a request attachment, a call recording.
 *
 * The bearer token lives in memory, not in a cookie the browser attaches on its own, so an `<a href>` to
 * these routes always answers 401. The bytes have to be fetched with the token and handed to the browser
 * as an object URL, which is what this does.
 */
export async function downloadGet(path: string, fallbackName = "document.pdf"): Promise<DownloadedFile> {
  return send(path, undefined, fallbackName);
}

/**
 * The API hands back ready-made `download_url`s (`/api/v1/portal/policies/<id>/document`) while screens that
 * know their own route pass a bare one (`/policies/<id>/document`). Both mean the same thing, and blindly
 * prefixing turned the first kind into `/api/v1/api/v1/...` — a 404 behind every portal download button,
 * which the browser reports as "no download happened" (ADR-025).
 */
export function downloadUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path; // an absolute/signed URL is already complete
  const trimmed = path.startsWith(`${BASE}/`) ? path.slice(BASE.length) : path;
  return `${BASE}${trimmed.startsWith("/") ? "" : "/"}${trimmed}`;
}

async function send(path: string, body?: string, fallbackName = "export.xlsx"): Promise<DownloadedFile> {
  const response = await fetch(downloadUrl(path), {
    method: body === undefined ? "GET" : "POST",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(getAccessToken() ? { authorization: `Bearer ${getAccessToken()}` } : {}),
    },
    body,
  });

  if (!response.ok) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null; // a non-JSON failure body still becomes the uniform error below
    }
    throw errorFrom(response.status, payload);
  }

  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? fallbackName;
  const rows = Number.parseInt(response.headers.get("x-export-rows") ?? "", 10);
  return {
    filename,
    blob: await response.blob(),
    rows: Number.isNaN(rows) ? null : rows,
    truncated: response.headers.get("x-export-truncated") === "1",
  };
}

function errorFrom(status: number, payload: unknown): ApiError_ {
  return new ApiError_(status, (payload ?? null) as Partial<ApiError> | null, `Request failed (${status})`);
}

/** Trigger the browser save, then release the object URL so the blob does not linger in memory. */
export function saveFile(file: DownloadedFile): void {
  const url = URL.createObjectURL(file.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  // Revoked on the next tick, not immediately: Chromium starts the download from the click but Firefox
  // reads the blob afterwards, and an eager `revokeObjectURL` turns a working button into an empty file.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
