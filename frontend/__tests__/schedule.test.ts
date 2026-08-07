import { describe, it, expect } from "vitest";
import { getCalendarBlocks, getFreeDays, getScheduleTimespan, getSectionColor, CALENDAR_DAYS } from "@/lib/schedule";
import { RankedSchedule, Section } from "@/lib/types";

function makeSection(overrides: Partial<Section> & { course_code: string }): Section {
  const { course_code, meeting_times, ...rest } = overrides;
  return {
    ref_no: "11111",
    course_code,
    section_id: "01",
    title: "Test Course",
    credits: "1",
    course_type: "Course",
    instructors: [],
    meeting_times: meeting_times ?? [],
    ...rest,
  };
}

function makeSchedule(overrides: Partial<RankedSchedule> = {}): RankedSchedule {
  return {
    schedule_id: "deadbeef00000001",
    parent_sections: [],
    linked_sections: [],
    total_credits: "3",
    score: 100,
    score_breakdown: { requirement_gains: 0, preferred_subjects: 0, free_days: 0, compactness: 0, credit_load: 0 },
    requirement_gains: [],
    category: "balanced",
    explanation: "",
    ...overrides,
  };
}

const MWF_SECTION = makeSection({
  course_code: "CPSC 035",
  meeting_times: [{ days: ["M", "W", "F"], start: "10:30", end: "11:20" }],
});

const TR_SECTION = makeSection({
  course_code: "MATH 027",
  meeting_times: [{ days: ["T", "R"], start: "13:15", end: "14:35" }],
});

describe("getCalendarBlocks", () => {
  it("produces one block per day-course pair", () => {
    const schedule = makeSchedule({ parent_sections: [MWF_SECTION] });
    const blocks = getCalendarBlocks(schedule);
    expect(blocks).toHaveLength(3); // M, W, F
    expect(blocks.map((b) => b.day)).toEqual(expect.arrayContaining(["M", "W", "F"]));
  });

  it("marks linked sections correctly", () => {
    const lab = makeSection({ course_code: "CPSC 035", course_type: "Lab", meeting_times: [{ days: ["R"], start: "14:00", end: "17:00" }] });
    const schedule = makeSchedule({ parent_sections: [MWF_SECTION], linked_sections: [lab] });
    const blocks = getCalendarBlocks(schedule);
    const linkedBlocks = blocks.filter((b) => b.isLinked);
    expect(linkedBlocks).toHaveLength(1);
    expect(linkedBlocks[0].day).toBe("R");
  });

  it("computes start/end minutes correctly", () => {
    const schedule = makeSchedule({ parent_sections: [MWF_SECTION] });
    const block = getCalendarBlocks(schedule)[0];
    expect(block.startMin).toBe(10 * 60 + 30); // 630
    expect(block.endMin).toBe(11 * 60 + 20);   // 680
  });
});

describe("getFreeDays", () => {
  it("returns all days for empty schedule", () => {
    const s = makeSchedule();
    expect(getFreeDays(s)).toEqual([...CALENDAR_DAYS]);
  });

  it("returns correct free days for MWF schedule", () => {
    const s = makeSchedule({ parent_sections: [MWF_SECTION] });
    const free = getFreeDays(s);
    // MWF course → T and R are free; M, W, F are busy
    expect(free).toContain("T");
    expect(free).toContain("R");
    expect(free).not.toContain("M");
    expect(free).not.toContain("W");
    expect(free).not.toContain("F");
  });

  it("returns no free days for schedule with all days", () => {
    const allDays = makeSection({
      course_code: "TEST 001",
      meeting_times: [{ days: ["M", "T", "W", "R", "F"], start: "10:00", end: "11:00" }],
    });
    const s = makeSchedule({ parent_sections: [allDays] });
    expect(getFreeDays(s)).toHaveLength(0);
  });
});

describe("getScheduleTimespan", () => {
  it("returns null for empty schedule", () => {
    const { earliest, latest } = getScheduleTimespan(makeSchedule());
    expect(earliest).toBeNull();
    expect(latest).toBeNull();
  });

  it("returns correct earliest and latest", () => {
    const s = makeSchedule({ parent_sections: [MWF_SECTION, TR_SECTION] });
    const { earliest, latest } = getScheduleTimespan(s);
    expect(earliest).toBe("10:30");
    expect(latest).toBe("14:35");
  });
});

describe("getSectionColor", () => {
  it("returns a string with css classes", () => {
    const color = getSectionColor("CPSC 035");
    expect(typeof color).toBe("string");
    expect(color.length).toBeGreaterThan(0);
  });

  it("returns the same color for same course code (deterministic)", () => {
    expect(getSectionColor("MATH 027")).toBe(getSectionColor("MATH 027"));
  });

  it("does not crash on empty string", () => {
    expect(() => getSectionColor("")).not.toThrow();
  });
});
