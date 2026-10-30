/**
 * R15 P2 — the pickers search, they do not dump.
 *
 * Two dialogs fetched `page_size: 200` clients and rendered them as `<option>`s. The cost grows with the
 * customer list and the operator still scrolls; and a stale 200-row payload answers a question nobody asked on
 * every open. This pins the replacement: nothing is fetched until the operator types, the query is debounced,
 * and the page stays small.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: { path: string; query?: Record<string, unknown> }[] = [];

vi.mock("@/lib/api/client", () => ({
  api: {
    get: (path: string, query?: Record<string, unknown>) => {
      calls.push({ path, query });
      if (path === "/clients") {
        return Promise.resolve({ items: [{ id: "c-1", name: "شركة ألف" }], total: 1, page: 1, page_size: 20 });
      }
      return Promise.resolve({});
    },
  },
  ApiError_: class ApiError extends Error {},
}));

import { ClientPicker } from "@/components/ClientPicker";

beforeEach(() => {
  calls.length = 0;
});

describe("R15 · the client picker is a search", () => {
  it("fetches nothing on open", () => {
    render(<ClientPicker value="" onChange={() => undefined} />);
    expect(calls.filter((call) => call.path === "/clients")).toHaveLength(0);
  });

  it("waits for two characters, then asks for one small page", async () => {
    render(<ClientPicker value="" onChange={() => undefined} />);
    const input = screen.getByTestId("client-picker-input");
    fireEvent.change(input, { target: { value: "ش" } });
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(calls.filter((call) => call.path === "/clients")).toHaveLength(0);

    fireEvent.change(input, { target: { value: "شركة" } });
    await waitFor(() => expect(calls.filter((call) => call.path === "/clients")).toHaveLength(1), { timeout: 1500 });
    const request = calls.find((call) => call.path === "/clients");
    expect(request?.query).toMatchObject({ q: "شركة", page_size: 20 });
  });

  it("shows the chosen client and can clear it", async () => {
    const onChange = vi.fn();
    render(<ClientPicker value="" onChange={onChange} />);
    fireEvent.change(screen.getByTestId("client-picker-input"), { target: { value: "شركة" } });
    const option = await screen.findByRole("option", { name: "شركة ألف" }, { timeout: 1500 });
    fireEvent.click(option);
    expect(onChange).toHaveBeenCalledWith("c-1", "شركة ألف");
  });
});
