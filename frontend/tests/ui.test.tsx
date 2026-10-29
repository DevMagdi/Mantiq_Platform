import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Avatar, Badge, Chip, DataTable, Segmented, StateBlock, Toggle } from "@/components/ui";

describe("design tokens in shared primitives", () => {
  it("renders badges with the tone palette (green/amber/red/blue/teal)", () => {
    render(<Badge tone="g">نشط</Badge>);
    const badge = screen.getByText("نشط");
    expect(badge).toHaveStyle({ background: "var(--g-bg)", color: "var(--g-ink)" });
  });

  it("keeps the avatar at the sheet's 38px and #C7D2FE", () => {
    const { container } = render(<Avatar name="منى فؤاد" />);
    const avatar = container.firstElementChild as HTMLElement;
    expect(avatar).toHaveTextContent("مف");
    expect(avatar).toHaveStyle({ width: "38px", height: "38px", background: "#C7D2FE" });
  });

  it("every state block explains itself instead of going blank (spec §9)", () => {
    for (const kind of ["loading", "empty", "error", "forbidden", "notfound"] as const) {
      const { container, unmount } = render(<StateBlock kind={kind} />);
      expect(container.textContent).not.toHaveLength(0);
      unmount();
    }
    render(<StateBlock kind="forbidden" />);
    // the copy is translated, never hardcoded inside the component
    expect(screen.getByText("لا تملك صلاحية")).toBeInTheDocument();
    expect(screen.getByText("هذه الشاشة غير متاحة لدورك.")).toBeInTheDocument();
  });

  it("toggle is a real switch and reports its change", async () => {
    const onChange = vi.fn();
    render(<Toggle checked={false} onChange={onChange} label="عرض كل الليدات" />);
    const control = screen.getByRole("switch", { name: "عرض كل الليدات" });
    expect(control).toHaveAttribute("aria-checked", "false");
    await userEvent.click(control);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("segmented control moves focus and selection with the keyboard (spec §10)", async () => {
    const onChange = vi.fn();
    render(
      <Segmented
        ariaLabel="تبويبات"
        value="a"
        onChange={onChange}
        options={[
          { value: "a", label: "الأول" },
          { value: "b", label: "الثاني" },
        ]}
      />,
    );
    screen.getByRole("tab", { name: "الثاني" }).focus();
    await userEvent.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith("b");
  });

  it("DataTable tints rows for converted and late records", () => {
    interface Row {
      id: string;
      name: string;
    }
    render(
      <DataTable<Row>
        rows={[
          { id: "1", name: "منطق" },
          { id: "2", name: "الأمل" },
        ]}
        keyOf={(row) => row.id}
        rowTone={(row) => (row.id === "1" ? "ok" : "warn")}
        columns={[{ id: "name", head: "العميل", render: (row) => row.name }]}
      />,
    );
    const [first, second] = screen.getAllByRole("row").slice(1);
    expect(first).toHaveStyle({ background: "var(--row-ok)" });
    expect(second).toHaveStyle({ background: "var(--row-warn)" });
  });

  it("chips carry their tone colour for follow-up states", () => {
    render(<Chip tone="r">تأخرت المتابعة</Chip>);
    expect(screen.getByText("تأخرت المتابعة")).toHaveStyle({ color: "var(--r-ink)" });
  });
});
