import { describe, it, expect } from "vitest";
import {
  formatCategoryLabel,
  formatScoreLabel,
  formatDayFull,
  formatDayAbbr,
  formatDayList,
  formatTime,
  formatTimeRange,
  timeToMinutes,
  formatCredits,
  requirementGainVerb,
  solverCapNotice,
} from "@/lib/formatting";

describe("formatCategoryLabel", () => {
  it("maps requirements_first", () => {
    expect(formatCategoryLabel("requirements_first")).toBe("Best for Degree Progress");
  });
  it("maps preferred_subjects", () => {
    expect(formatCategoryLabel("preferred_subjects")).toBe("Best for Your Subject Preferences");
  });
  it("maps compact_schedule", () => {
    expect(formatCategoryLabel("compact_schedule")).toBe("Most Compact Week");
  });
  it("maps balanced", () => {
    expect(formatCategoryLabel("balanced")).toBe("Most Balanced");
  });
  it("maps current_registration", () => {
    expect(formatCategoryLabel("current_registration")).toBe("Your Current Registration");
  });
  it("returns raw value for unknown category", () => {
    expect(formatCategoryLabel("unknown_xyz")).toBe("unknown_xyz");
  });
});

describe("formatScoreLabel", () => {
  it("maps requirement_gains", () => {
    expect(formatScoreLabel("requirement_gains")).toBe("Degree progress");
  });
  it("maps preferred_subjects", () => {
    expect(formatScoreLabel("preferred_subjects")).toBe("Subject preferences");
  });
  it("maps free_days", () => {
    expect(formatScoreLabel("free_days")).toBe("Free-day fit");
  });
  it("maps compactness", () => {
    expect(formatScoreLabel("compactness")).toBe("Schedule compactness");
  });
  it("maps credit_load", () => {
    expect(formatScoreLabel("credit_load")).toBe("Credit target");
  });
});

describe("weekday formatting", () => {
  it("formats M as Monday", () => expect(formatDayFull("M")).toBe("Monday"));
  it("formats R as Thursday", () => expect(formatDayFull("R")).toBe("Thursday"));
  it("abbreviates T as Tue", () => expect(formatDayAbbr("T")).toBe("Tue"));
  it("abbreviates F as Fri", () => expect(formatDayAbbr("F")).toBe("Fri"));

  it("formats day list with one day", () => {
    expect(formatDayList(["M"])).toBe("Monday");
  });
  it("formats day list with two days", () => {
    expect(formatDayList(["M", "W"])).toBe("Mon & Wednesday");
  });
  it("returns empty string for empty list", () => {
    expect(formatDayList([])).toBe("");
  });
});

describe("time formatting", () => {
  it("formats 10:30 AM", () => expect(formatTime("10:30")).toBe("10:30 AM"));
  it("formats 00:00 as midnight", () => expect(formatTime("00:00")).toBe("12:00 AM"));
  it("formats 12:00 as noon", () => expect(formatTime("12:00")).toBe("12:00 PM"));
  it("formats 13:15 as 1:15 PM", () => expect(formatTime("13:15")).toBe("1:15 PM"));
  it("formats range", () => expect(formatTimeRange("09:00", "10:15")).toBe("9:00 AM–10:15 AM"));

  it("converts 10:30 to 630 minutes", () => expect(timeToMinutes("10:30")).toBe(630));
  it("converts 08:00 to 480 minutes", () => expect(timeToMinutes("08:00")).toBe(480));
});

describe("formatCredits", () => {
  it("formats whole number without decimal", () => expect(formatCredits("3")).toBe("3"));
  it("formats decimal credit", () => expect(formatCredits("3.5")).toBe("3.5"));
  it("formats string that parses as whole", () => expect(formatCredits("4.0")).toBe("4"));
});

describe("requirementGainVerb", () => {
  it("never says completes", () => {
    const text = requirementGainVerb();
    expect(text).not.toMatch(/complet/i);
    expect(text).not.toMatch(/fulfil/i);
    expect(text).not.toMatch(/ensures graduation/i);
  });
  it("returns a non-empty string", () => {
    expect(requirementGainVerb().length).toBeGreaterThan(0);
  });
});

describe("solverCapNotice", () => {
  it("says highest-ranked, not best possible", () => {
    const text = solverCapNotice();
    expect(text).toMatch(/highest-ranked/i);
    expect(text).not.toMatch(/best possible/i);
    expect(text).not.toMatch(/global optimum.*guaranteed/i);
  });
});
