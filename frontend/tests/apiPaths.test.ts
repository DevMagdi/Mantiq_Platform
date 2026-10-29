/**
 * Every API path the UI calls must exist in the API.
 *
 * `StaffShell` shipped a sign-out that called `POST /portal/sign-out`. No such route has ever existed, so
 * every staff sign-out made a 404 that the console showed and nobody read. Nothing caught it, because a
 * string is only a string until it reaches a server.
 *
 * This test closes that gap statically: it scans `src/` for `api.get/post/patch/put/del/upload("<path>")`
 * and `api.upload("<path>")`, normalises the query string away, and insists each path is a key (or matches a
 * templated key) in `docs/api/openapi.json` — the same document `schema.d.ts` is generated from. No network,
 * no server, runs in milliseconds.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = resolve(import.meta.dirname, "../src");
const OPENAPI = resolve(import.meta.dirname, "../../docs/api/openapi.json");

/** `api.get<T>("/leads", …)` / `api.post("/auth/logout")` / `api.upload("/leads/import", form)` */
const CALL = /\bapi\.(get|post|patch|put|del|upload)<[^>]*>?\(\s*["'`]([^"'`]+)["'`]/g;
/** Same calls without a generic parameter. */
const CALL_PLAIN = /\bapi\.(get|post|patch|put|del|upload)\(\s*["'`]([^"'`]+)["'`]/g;

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...sourceFiles(full));
    else if (/\.(tsx?|jsx?)$/.test(entry)) out.push(full);
  }
  return out;
}

interface Call {
  file: string;
  line: number;
  verb: string;
  path: string;
}

function callsIn(dir: string): Call[] {
  const calls: Call[] = [];
  for (const file of sourceFiles(dir)) {
    const text = readFileSync(file, "utf8");
    for (const pattern of [CALL, CALL_PLAIN]) {
      for (const match of text.matchAll(pattern)) {
        const index = match.index ?? 0;
        const verb = match[1] ?? "";
        const raw = match[2] ?? "";
        calls.push({
          file: file.replace(`${SRC}/`, ""),
          line: text.slice(0, index).split("\n").length,
          verb,
          // `/leads?only_open=true` is one route; the query is not part of the path
          path: raw.split("?")[0] ?? "",
        });
      }
    }
  }
  return calls;
}

/** The document's paths, minus the version prefix the client adds itself (`BASE = "/api/v1"`). */
function documentedPaths(): string[] {
  const spec = JSON.parse(readFileSync(OPENAPI, "utf8")) as { paths: Record<string, unknown> };
  return Object.keys(spec.paths).map((path) => path.replace(/^\/api\/v1/, ""));
}

/**
 * `{id}` (OpenAPI) and `${leadId}` (a template literal) are the same placeholder, so both are lifted to
 * `{}` before comparing — otherwise every templated path reads as "not documented".
 */
function matches(concrete: string, template: string): boolean {
  const lift = (value: string) => value.replace(/\$\{[^}]+\}/g, "{}").replace(/\{[^}]+\}/g, "{}");
  return lift(concrete) === lift(template);
}

describe("every API path the UI calls exists in OpenAPI", () => {
  const calls = callsIn(SRC);
  const documented = documentedPaths();

  it("finds the calls it is supposed to check (guards against a silent no-op)", () => {
    // If a refactor changes the client's shape, this test must fail loudly instead of passing on zero calls.
    expect(calls.length).toBeGreaterThan(40);
    expect(documented.length).toBeGreaterThan(100);
  });

  it("has no call to an undocumented route", () => {
    const unknown = calls
      .filter((call) => !documented.some((path) => matches(call.path, path)))
      .map((call) => `${call.file}:${call.line}  api.${call.verb}("${call.path}")`);

    expect(unknown, "these paths do not exist in docs/api/openapi.json").toEqual([]);
  });

  it("names each route with the method the API documents", () => {
    const spec = JSON.parse(readFileSync(OPENAPI, "utf8")) as {
      paths: Record<string, Record<string, unknown>>;
    };
    const verbOf: Record<string, string> = { get: "get", post: "post", patch: "patch", put: "put", del: "delete" };
    const wrong: string[] = [];
    for (const call of calls) {
      const key = Object.keys(spec.paths).find((path) => matches(call.path, path.replace(/^\/api\/v1/, "")));
      if (!key) continue; // reported by the test above
      const verbs = Object.keys(spec.paths[key] ?? {});
      const documentedVerb = verbOf[call.verb] ?? "post";
      // `upload` is a multipart POST; `post` covers both
      const wants = call.verb === "upload" ? "post" : documentedVerb;
      if (!verbs.includes(wants)) {
        wrong.push(`${call.file}:${call.line}  api.${call.verb}("${call.path}") — ${key} has ${verbs.join("/")}`);
      }
    }
    expect(wrong, "the UI uses a method the API does not offer on that path").toEqual([]);
  });
});
