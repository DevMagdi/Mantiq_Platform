/**
 * `components/Modal` — the dialog every confirm, import preview and redistribute picker sits in.
 *
 * R3 measured the phone project spending its full 60 s budget on «تنفيذ الاستيراد»: Playwright kept reporting
 * the button "visible, enabled and stable" and "outside of the viewport". The cause was structural — the
 * overlay centred an *uncapped* panel, so a tall dialog (preview table + stats) grew past the screen, the
 * overlay scrolled instead of the panel, and the footer's confirm button could never be brought into view.
 * jsdom has no layout, so what is pinned here is the structure that makes the fix hold in a browser:
 * the panel is height-capped, the scroll box is inside it, and the footer is a sibling of that box (so it is
 * reachable while the body scrolls).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Modal } from "@/components/Modal";

describe("Modal", () => {
  it("caps the panel and scrolls its body, keeping the actions out of the scroll box", () => {
    render(
      <Modal title="إعادة توزيع الـLeads" onClose={() => undefined} actions={<button type="button">وزّع</button>}>
        <p>محتوى طويل</p>
      </Modal>,
    );
    const dialog = screen.getByRole("dialog", { name: "إعادة توزيع الـLeads" });
    // the overlay itself may scroll (tiny screens), and the panel may not exceed the visible height
    expect(dialog.className).toContain("overflow-y-auto");
    const panel = dialog.firstElementChild as HTMLElement;
    expect(panel.className, "the panel must be capped to the viewport").toContain("max-h-[calc(100dvh-2rem)]");
    expect(panel.className).toContain("overflow-hidden");

    const body = screen.getByText("محتوى طويل").parentElement as HTMLElement;
    expect(body.className, "the report scrolls inside the panel").toContain("overflow-y-auto");
    const actions = screen.getByRole("button", { name: "وزّع" }).parentElement as HTMLElement;
    expect(actions.tagName).toBe("FOOTER");
    // a sibling of the scroll box — never inside it, or it would scroll away with the table
    expect(actions.parentElement).toBe(panel);
    expect(body.parentElement).toBe(panel);
  });

  it("closes on Escape and on a backdrop press, but not on a press inside the panel", () => {
    const onClose = vi.fn();
    render(
      <Modal title="تأكيد" onClose={onClose}>
        <button type="button">داخل</button>
      </Modal>,
    );
    fireEvent.mouseDown(screen.getByRole("button", { name: "داخل" }));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(screen.getByRole("dialog", { name: "تأكيد" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("names the close button for its action, so it is not a second «إغلاق»", () => {
    render(
      <Modal title="تأكيد" onClose={() => undefined}>
        <p>نص</p>
      </Modal>,
    );
    const buttons = screen.getAllByRole("button").map((button) => button.getAttribute("aria-label") ?? button.textContent);
    expect(buttons).toEqual([expect.stringContaining("إغلاق")]);
    expect(buttons[0]).not.toBe("إغلاق");
  });
});
