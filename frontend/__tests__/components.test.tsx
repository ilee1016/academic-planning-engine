import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { FileUploadCard } from "@/components/upload/FileUploadCard";
import { PreferencesForm, defaultPreferences } from "@/components/planner/PreferencesForm";
import { LockedSectionResolver } from "@/components/planner/LockedSectionResolver";
import { ScheduleCard } from "@/components/schedules/ScheduleCard";
import { requirementGainVerb } from "@/lib/formatting";
import { RankedSchedule } from "@/lib/types";

// ---------------------------------------------------------------------------
// FileUploadCard
// ---------------------------------------------------------------------------

describe("FileUploadCard", () => {
  it("renders browse button and label", () => {
    render(
      <FileUploadCard
        id="audit"
        label="Degree Works audit"
        description="Your PDF file"
        accept=".pdf"
        acceptLabel="PDF"
        file={null}
        onFileChange={() => {}}
      />,
    );
    expect(screen.getByText("Degree Works audit")).toBeInTheDocument();
    expect(screen.getByText(/browse/i)).toBeInTheDocument();
  });

  it("shows file name when file is provided", () => {
    const file = new File(["content"], "audit.pdf", { type: "application/pdf" });
    render(
      <FileUploadCard
        id="audit"
        label="Degree Works audit"
        description="desc"
        accept=".pdf"
        acceptLabel="PDF"
        file={file}
        onFileChange={() => {}}
      />,
    );
    expect(screen.getByText("audit.pdf")).toBeInTheDocument();
  });

  it("rejects invalid file extension and calls onFileChange with null", async () => {
    const onChange = vi.fn();
    render(
      <FileUploadCard
        id="audit"
        label="Degree Works audit"
        description="desc"
        accept=".pdf"
        acceptLabel="PDF"
        file={null}
        onFileChange={onChange}
      />,
    );
    const input = document.getElementById("audit") as HTMLInputElement;
    const badFile = new File(["text"], "catalog.csv", { type: "text/csv" });
    // Simulate file input change.
    Object.defineProperty(input, "files", { value: [badFile], writable: false });
    fireEvent.change(input);
    expect(onChange).toHaveBeenCalledWith(null);
    expect(await screen.findByText(/must be a PDF file/i)).toBeInTheDocument();
  });

  it("shows Replace and Remove buttons when file is loaded", () => {
    const file = new File(["content"], "audit.pdf", { type: "application/pdf" });
    render(
      <FileUploadCard
        id="audit"
        label="Degree Works audit"
        description="desc"
        accept=".pdf"
        acceptLabel="PDF"
        file={file}
        onFileChange={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /replace/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /remove/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// PreferencesForm
// ---------------------------------------------------------------------------

describe("PreferencesForm", () => {
  it("renders credit range inputs", () => {
    render(
      <PreferencesForm
        value={defaultPreferences()}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText(/minimum credits/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/maximum credits/i)).toBeInTheDocument();
  });

  it("renders all five weekday buttons", () => {
    render(
      <PreferencesForm
        value={defaultPreferences()}
        onChange={() => {}}
      />,
    );
    for (const day of ["Mon", "Tue", "Wed", "Thu", "Fri"]) {
      expect(screen.getByRole("button", { name: day })).toBeInTheDocument();
    }
  });

  it("validates max < min credits", async () => {
    const user = userEvent.setup();
    function StatefulPrefs() {
      const [val, setVal] = useState({ ...defaultPreferences(), minCredits: "4", maxCredits: "4" });
      return <PreferencesForm value={val} onChange={setVal} />;
    }
    render(<StatefulPrefs />);
    const maxInput = screen.getByLabelText(/maximum credits/i);
    await user.clear(maxInput);
    await user.type(maxInput, "2");
    expect(await screen.findByText(/maximum must be ≥ minimum/i)).toBeInTheDocument();
  });

  it("validates time window ordering", async () => {
    const user = userEvent.setup();
    render(
      <PreferencesForm
        value={{ ...defaultPreferences(), earliestStart: "14:00", latestEnd: "09:00" }}
        onChange={() => {}}
      />,
    );
    const end = screen.getByLabelText(/no classes after/i);
    await user.clear(end);
    await user.type(end, "08:00");
    expect(await screen.findByText(/earliest start must be before latest end/i)).toBeInTheDocument();
  });

  it("renders lock preregistered checkbox", () => {
    render(
      <PreferencesForm
        value={defaultPreferences()}
        onChange={() => {}}
      />,
    );
    expect(screen.getByLabelText(/keep my current registration/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// LockedSectionResolver
// ---------------------------------------------------------------------------

describe("LockedSectionResolver", () => {
  const courses = [
    {
      course_code: "CPSC 031",
      choices: [
        { ref_no: "11111", section_id: "01", meeting_times: [{ days: ["M", "W", "F"], start: "10:30", end: "11:20" }] },
        { ref_no: "22222", section_id: "02", meeting_times: [{ days: ["T", "R"], start: "13:15", end: "14:35" }] },
      ],
    },
  ];

  it("asks which section user is registered for", () => {
    render(
      <LockedSectionResolver
        courses={courses}
        selections={{}}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText(/which section are you registered for/i)).toBeInTheDocument();
  });

  it("displays section IDs for each choice", () => {
    render(
      <LockedSectionResolver
        courses={courses}
        selections={{}}
        onSelect={() => {}}
      />,
    );
    expect(screen.getByText(/section 01/i)).toBeInTheDocument();
    expect(screen.getByText(/section 02/i)).toBeInTheDocument();
  });

  it("calls onSelect when radio is chosen", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <LockedSectionResolver
        courses={courses}
        selections={{}}
        onSelect={onSelect}
      />,
    );
    const radio = screen.getByRole("radio", { name: /section 01/i });
    await user.click(radio);
    expect(onSelect).toHaveBeenCalledWith("CPSC 031", "11111");
  });

  it("does not display ref_no as primary label", () => {
    render(
      <LockedSectionResolver
        courses={courses}
        selections={{}}
        onSelect={() => {}}
      />,
    );
    // ref_no should not be the visible label text
    expect(screen.queryByText("11111")).not.toBeInTheDocument();
    expect(screen.queryByText("22222")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// ScheduleCard
// ---------------------------------------------------------------------------

function makeSchedule(overrides: Partial<RankedSchedule> = {}): RankedSchedule {
  return {
    schedule_id: "abc123",
    parent_sections: [
      {
        ref_no: "11111",
        course_code: "CPSC 031",
        section_id: "01",
        title: "Comp Org & Architecture",
        credits: "1",
        course_type: "Course",
        instructors: ["Smith, Jane"],
        meeting_times: [{ days: ["M", "W", "F"], start: "10:30", end: "11:20" }],
      },
    ],
    linked_sections: [],
    total_credits: "3",
    score: 300,
    score_breakdown: { requirement_gains: 200, preferred_subjects: 0, free_days: 0, compactness: 100, credit_load: 0 },
    requirement_gains: [{ id: "cs_major_cpsc", label: "CS Major CPSC", notes: "" }],
    category: "requirements_first",
    explanation: "",
    ...overrides,
  };
}

describe("ScheduleCard", () => {
  it("shows course code", () => {
    render(<ScheduleCard schedule={makeSchedule()} sessionId="s1" />);
    expect(screen.getByText("CPSC 031")).toBeInTheDocument();
  });

  it("shows credit total", () => {
    render(<ScheduleCard schedule={makeSchedule()} sessionId="s1" />);
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows human-friendly category label not raw machine string", () => {
    render(<ScheduleCard schedule={makeSchedule()} sessionId="s1" />);
    expect(screen.getByText("Best for Degree Progress")).toBeInTheDocument();
    expect(screen.queryByText("requirements_first")).not.toBeInTheDocument();
  });

  it(`uses "${requirementGainVerb()}" wording for requirement gains`, () => {
    render(<ScheduleCard schedule={makeSchedule()} sessionId="s1" />);
    expect(screen.getByText(requirementGainVerb())).toBeInTheDocument();
  });

  it("does not say completes for requirement gains", () => {
    render(<ScheduleCard schedule={makeSchedule()} sessionId="s1" />);
    expect(screen.queryByText(/complet/i)).not.toBeInTheDocument();
  });

  it("shows requirement gain label", () => {
    render(<ScheduleCard schedule={makeSchedule()} sessionId="s1" />);
    expect(screen.getByText("CS Major CPSC")).toBeInTheDocument();
  });

  it("shows explanation trigger button initially", () => {
    render(<ScheduleCard schedule={makeSchedule()} sessionId="s1" />);
    expect(screen.getByRole("button", { name: /why this schedule/i })).toBeInTheDocument();
  });

  it("does not show calendar by default (collapsed)", () => {
    render(<ScheduleCard schedule={makeSchedule()} sessionId="s1" />);
    // Calendar should be hidden until expanded.
    const calendarToggle = screen.getByRole("button", { name: /show weekly calendar/i });
    expect(calendarToggle).toBeInTheDocument();
    expect(calendarToggle.getAttribute("aria-expanded")).toBe("false");
  });
});
