"use client";

import { AmbiguousCourse } from "@/lib/types";
import { formatDayAbbr, formatTimeRange } from "@/lib/formatting";
import { cn } from "@/lib/schedule";

interface LockedSectionResolverProps {
  courses: AmbiguousCourse[];
  selections: Record<string, string>; // course_code → chosen ref_no
  onSelect: (courseCode: string, refNo: string) => void;
}

export function LockedSectionResolver({ courses, selections, onSelect }: LockedSectionResolverProps) {
  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
        <h3 className="font-semibold text-amber-900">Which section are you registered for?</h3>
        <p className="mt-1 text-sm text-amber-700">
          One or more of your preregistered courses has multiple available sections.
          Please select the specific section you are enrolled in.
        </p>
      </div>

      {courses.map((course) => (
        <fieldset key={course.course_code} className="space-y-2">
          <legend className="text-sm font-semibold text-slate-800">
            {course.course_code}
          </legend>
          <div className="space-y-2" role="radiogroup" aria-label={`Section for ${course.course_code}`}>
            {course.choices.map((choice) => {
              const selected = selections[course.course_code] === choice.ref_no;
              return (
                <label
                  key={choice.ref_no}
                  className={cn(
                    "flex cursor-pointer items-start gap-3 rounded-xl border p-4 transition-colors",
                    selected
                      ? "border-slate-700 bg-slate-50"
                      : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50",
                  )}
                >
                  <input
                    type="radio"
                    name={`section-${course.course_code}`}
                    value={choice.ref_no}
                    checked={selected}
                    onChange={() => onSelect(course.course_code, choice.ref_no)}
                    className="mt-1 accent-slate-700"
                    aria-label={`Section ${choice.section_id}`}
                  />
                  <div>
                    <p className="font-medium text-slate-800">Section {choice.section_id}</p>
                    {choice.meeting_times.length > 0 ? (
                      <ul className="mt-1 space-y-0.5">
                        {choice.meeting_times.map((mt, i) => (
                          <li key={i} className="text-sm text-slate-600">
                            {mt.days.map(formatDayAbbr).join(", ")}{" "}
                            {formatTimeRange(mt.start, mt.end)}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-slate-400">No meeting times listed</p>
                    )}
                  </div>
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
    </div>
  );
}
