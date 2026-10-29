import { describe, expect, it } from "vitest";

import {
  daysUntil,
  followupLabel,
  formatDuration,
  formatMoney,
  formatNumber,
  formatPercent,
  humanDuration,
  initials,
  maskMobile,
  relativeTime,
  responseRating,
} from "@/lib/format";

describe("Egyptian formatting (spec §11)", () => {
  it("groups thousands with western digits", () => {
    expect(formatNumber(1240)).toBe("1,240");
    expect(formatNumber(null)).toBe("—");
  });

  it("renders money in EGP and percent with the Arabic sign", () => {
    expect(formatMoney(12400)).toBe("12,400 ج.م");
    expect(formatMoney("832135.00")).toBe("832,135 ج.م");
    expect(formatPercent(93.75)).toBe("93.8٪");
    expect(formatPercent(null)).toBe("—");
  });

  it("formats voice durations for the 20-bar player", () => {
    expect(formatDuration(65)).toBe("1:05");
    expect(formatDuration(0)).toBe("0:00");
  });

  it("keeps relative time short enough for a 330px list", () => {
    const now = new Date("2026-10-26T12:00:00Z");
    expect(relativeTime("2026-10-26T11:30:00Z", now)).toBe("منذ 30 د");
    expect(relativeTime("2026-10-26T09:00:00Z", now)).toBe("منذ 3 س");
    expect(relativeTime(null, now)).toBe("—");
  });

  it("masks mobile numbers in tables but keeps the tail the design shows", () => {
    expect(maskMobile("+201001234567")).toBe("01xxxxxxxxx");
    expect(maskMobile("+201001234567", 1)).toBe("01xxxxxxxx7");
    expect(maskMobile(null)).toBe("—");
  });

  it("writes SLA prose in the right language with Arabic dual forms", () => {
    expect(humanDuration(130, "ar")).toBe("ساعتان و 10 دقائق");
    expect(humanDuration(60, "ar")).toBe("ساعة");
    expect(humanDuration(45, "ar")).toBe("45 دقيقة");
    expect(humanDuration(130, "en")).toBe("2 h 10 min");
  });

  it("labels follow-ups as today / tomorrow / late", () => {
    const now = new Date("2026-10-26T09:00:00Z");
    const labels = { today: "اليوم", tomorrow: "غدًا", late: "تأخرت", next: "التالية" };
    expect(followupLabel("2026-10-26T15:00:00Z", labels, now)).toEqual({ text: "اليوم", tone: "a" });
    expect(followupLabel("2026-10-27T15:00:00Z", labels, now)).toEqual({ text: "غدًا", tone: "b" });
    expect(followupLabel("2026-10-20T15:00:00Z", labels, now)).toEqual({ text: "تأخرت", tone: "r" });
    expect(followupLabel(null, labels, now)).toEqual({ text: "التالية", tone: "b" });
  });

  it("initials and renewal countdown", () => {
    expect(initials("منى عبد الله")).toBe("مع");
    expect(initials("")).toBe("؟");
    expect(daysUntil("2026-10-31", new Date("2026-10-26T00:00:00Z"))).toBe(5);
    expect(daysUntil(null)).toBeNull();
  });
});


describe("responseRating (portal quality label)", () => {
  it("is derived from the measured average, not hardcoded", () => {
    expect(responseRating(12)).toBe("excellent");
    expect(responseRating(30)).toBe("excellent");
    expect(responseRating(31)).toBe("good");
    expect(responseRating(45)).toBe("good");
    expect(responseRating(180)).toBe("slow");
    expect(responseRating(null)).toBeNull();
    expect(responseRating(Number.NaN)).toBeNull();
  });
});
