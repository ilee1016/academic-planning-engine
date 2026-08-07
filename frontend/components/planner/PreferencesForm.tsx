"use client";

import { useState, KeyboardEvent } from "react";
import { PreferencesRequest } from "@/lib/types";
import { cn } from "@/lib/schedule";

const WEEKDAYS = [
  { code: "M", label: "Mon" },
  { code: "T", label: "Tue" },
  { code: "W", label: "Wed" },
  { code: "R", label: "Thu" },
  { code: "F", label: "Fri" },
];

export interface PreferencesFormValue {
  minCredits: string;
  maxCredits: string;
  earliestStart: string;
  latestEnd: string;
  freeDays: string[];
  preferredSubjects: string[];
  excludedCourses: string[];
  lockPreregistered: boolean;
}

export function defaultPreferences(): PreferencesFormValue {
  return {
    minCredits: "3",
    maxCredits: "4",
    earliestStart: "",
    latestEnd: "",
    freeDays: [],
    preferredSubjects: [],
    excludedCourses: [],
    lockPreregistered: true,
  };
}

export function toPreferencesRequest(v: PreferencesFormValue): PreferencesRequest {
  return {
    min_credits: v.minCredits,
    max_credits: v.maxCredits,
    earliest_start: v.earliestStart || null,
    latest_end: v.latestEnd || null,
    free_days: v.freeDays,
    preferred_subjects: v.preferredSubjects,
    excluded_courses: v.excludedCourses,
    lock_preregistered: v.lockPreregistered,
  };
}

interface PreferencesFormProps {
  value: PreferencesFormValue;
  onChange: (v: PreferencesFormValue) => void;
  disabled?: boolean;
}

export function PreferencesForm({ value, onChange, disabled = false }: PreferencesFormProps) {
  const [subjectInput, setSubjectInput] = useState("");
  const [excludeInput, setExcludeInput] = useState("");
  const [creditError, setCreditError] = useState<string | null>(null);
  const [timeError, setTimeError] = useState<string | null>(null);

  const set = (patch: Partial<PreferencesFormValue>) => onChange({ ...value, ...patch });

  const validateCredits = (min: string, max: string): string | null => {
    const mn = parseFloat(min);
    const mx = parseFloat(max);
    if (isNaN(mn) || mn < 0) return "Minimum credits must be ≥ 0.";
    if (isNaN(mx)) return "Maximum credits is required.";
    if (mx < mn) return "Maximum must be ≥ minimum.";
    if (mx > 8) return "Maximum credits cannot exceed 8.";
    return null;
  };

  const validateTime = (start: string, end: string): string | null => {
    if (start && end) {
      const [sh, sm] = start.split(":").map(Number);
      const [eh, em] = end.split(":").map(Number);
      if (sh * 60 + sm >= eh * 60 + em) {
        return "Earliest start must be before latest end.";
      }
    }
    return null;
  };

  const handleMinCredits = (v: string) => {
    set({ minCredits: v });
    setCreditError(validateCredits(v, value.maxCredits));
  };

  const handleMaxCredits = (v: string) => {
    set({ maxCredits: v });
    setCreditError(validateCredits(value.minCredits, v));
  };

  const handleEarliestStart = (v: string) => {
    set({ earliestStart: v });
    setTimeError(validateTime(v, value.latestEnd));
  };

  const handleLatestEnd = (v: string) => {
    set({ latestEnd: v });
    setTimeError(validateTime(value.earliestStart, v));
  };

  const toggleFreeDay = (day: string) => {
    const next = value.freeDays.includes(day)
      ? value.freeDays.filter((d) => d !== day)
      : [...value.freeDays, day];
    set({ freeDays: next });
  };

  const addChip = (
    input: string,
    field: "preferredSubjects" | "excludedCourses",
    setter: (v: string) => void,
  ) => {
    const val = input.trim().toUpperCase();
    if (!val) return;
    if (!value[field].includes(val)) {
      set({ [field]: [...value[field], val] });
    }
    setter("");
  };

  const removeChip = (field: "preferredSubjects" | "excludedCourses", item: string) => {
    set({ [field]: value[field].filter((v) => v !== item) });
  };

  const handleSubjectKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addChip(subjectInput, "preferredSubjects", setSubjectInput);
    }
  };

  const handleExcludeKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addChip(excludeInput, "excludedCourses", setExcludeInput);
    }
  };

  const fieldClass = cn(
    "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400",
    "focus:border-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-200",
    disabled && "opacity-50 cursor-not-allowed",
  );

  return (
    <div className="space-y-6">
      {/* Credit range */}
      <fieldset>
        <legend className="text-sm font-semibold text-slate-800">Credit range</legend>
        <div className="mt-2 grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="min-credits" className="block text-xs font-medium text-slate-600 mb-1">
              Minimum credits
            </label>
            <input
              id="min-credits"
              type="number"
              min="0"
              max="8"
              step="0.5"
              value={value.minCredits}
              onChange={(e) => handleMinCredits(e.target.value)}
              className={fieldClass}
              disabled={disabled}
            />
          </div>
          <div>
            <label htmlFor="max-credits" className="block text-xs font-medium text-slate-600 mb-1">
              Maximum credits
            </label>
            <input
              id="max-credits"
              type="number"
              min="0"
              max="8"
              step="0.5"
              value={value.maxCredits}
              onChange={(e) => handleMaxCredits(e.target.value)}
              className={fieldClass}
              disabled={disabled}
            />
          </div>
        </div>
        {creditError && (
          <p className="mt-1 text-xs text-red-600" role="alert">{creditError}</p>
        )}
      </fieldset>

      {/* Free days */}
      <fieldset>
        <legend className="text-sm font-semibold text-slate-800">
          Keep these days free
        </legend>
        <p className="mt-0.5 text-xs text-slate-500">Selected days will have no classes scheduled.</p>
        <div className="mt-2 flex gap-2" role="group" aria-label="Free days">
          {WEEKDAYS.map((d) => (
            <button
              key={d.code}
              type="button"
              onClick={() => toggleFreeDay(d.code)}
              disabled={disabled}
              aria-pressed={value.freeDays.includes(d.code)}
              className={cn(
                "flex-1 rounded-lg border py-2 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400",
                value.freeDays.includes(d.code)
                  ? "border-slate-700 bg-slate-700 text-white"
                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50",
                disabled && "opacity-50 cursor-not-allowed",
              )}
            >
              {d.label}
            </button>
          ))}
        </div>
      </fieldset>

      {/* Time window */}
      <fieldset>
        <legend className="text-sm font-semibold text-slate-800">Class time window</legend>
        <div className="mt-2 grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="earliest-start" className="block text-xs font-medium text-slate-600 mb-1">
              No classes before
            </label>
            <input
              id="earliest-start"
              type="time"
              value={value.earliestStart}
              onChange={(e) => handleEarliestStart(e.target.value)}
              className={fieldClass}
              disabled={disabled}
            />
          </div>
          <div>
            <label htmlFor="latest-end" className="block text-xs font-medium text-slate-600 mb-1">
              No classes after
            </label>
            <input
              id="latest-end"
              type="time"
              value={value.latestEnd}
              onChange={(e) => handleLatestEnd(e.target.value)}
              className={fieldClass}
              disabled={disabled}
            />
          </div>
        </div>
        {timeError && (
          <p className="mt-1 text-xs text-red-600" role="alert">{timeError}</p>
        )}
      </fieldset>

      {/* Preferred subjects */}
      <div>
        <label htmlFor="preferred-subjects" className="block text-sm font-semibold text-slate-800">
          Preferred subjects
        </label>
        <p className="mt-0.5 text-xs text-slate-500">
          Subject codes (e.g. CPSC, MATH). Boosts ranking but does not guarantee inclusion.
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {value.preferredSubjects.map((s) => (
            <span
              key={s}
              className="inline-flex items-center gap-1 rounded-full bg-blue-100 px-3 py-1 text-xs font-medium text-blue-800"
            >
              {s}
              <button
                type="button"
                onClick={() => removeChip("preferredSubjects", s)}
                disabled={disabled}
                className="ml-0.5 rounded-full hover:text-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-500"
                aria-label={`Remove ${s}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <div className="mt-2 flex gap-2">
          <input
            id="preferred-subjects"
            type="text"
            placeholder="CPSC, MATH, …"
            value={subjectInput}
            onChange={(e) => setSubjectInput(e.target.value)}
            onKeyDown={handleSubjectKey}
            className={cn(fieldClass, "flex-1")}
            disabled={disabled}
            aria-describedby="preferred-subjects-hint"
          />
          <button
            type="button"
            onClick={() => addChip(subjectInput, "preferredSubjects", setSubjectInput)}
            disabled={disabled || !subjectInput.trim()}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-40"
          >
            Add
          </button>
        </div>
        <p id="preferred-subjects-hint" className="mt-1 text-xs text-slate-400">
          Press Enter or comma to add
        </p>
      </div>

      {/* Excluded courses */}
      <div>
        <label htmlFor="excluded-courses" className="block text-sm font-semibold text-slate-800">
          Excluded courses
        </label>
        <p className="mt-0.5 text-xs text-slate-500">
          Courses that will not appear in generated schedules (e.g. CPSC 035).
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          {value.excludedCourses.map((c) => (
            <span
              key={c}
              className="inline-flex items-center gap-1 rounded-full bg-red-100 px-3 py-1 text-xs font-medium text-red-800"
            >
              {c}
              <button
                type="button"
                onClick={() => removeChip("excludedCourses", c)}
                disabled={disabled}
                className="ml-0.5 rounded-full hover:text-red-600 focus:outline-none focus:ring-1 focus:ring-red-500"
                aria-label={`Remove ${c}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <div className="mt-2 flex gap-2">
          <input
            id="excluded-courses"
            type="text"
            placeholder="CPSC 035, MATH 027, …"
            value={excludeInput}
            onChange={(e) => setExcludeInput(e.target.value)}
            onKeyDown={handleExcludeKey}
            className={cn(fieldClass, "flex-1")}
            disabled={disabled}
            aria-describedby="excluded-courses-hint"
          />
          <button
            type="button"
            onClick={() => addChip(excludeInput, "excludedCourses", setExcludeInput)}
            disabled={disabled || !excludeInput.trim()}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-40"
          >
            Add
          </button>
        </div>
        <p id="excluded-courses-hint" className="mt-1 text-xs text-slate-400">
          Press Enter or comma to add
        </p>
      </div>

      {/* Lock preregistered */}
      <div className="flex items-start gap-3">
        <input
          id="lock-preregistered"
          type="checkbox"
          checked={value.lockPreregistered}
          onChange={(e) => set({ lockPreregistered: e.target.checked })}
          disabled={disabled}
          className="mt-0.5 h-4 w-4 cursor-pointer accent-slate-700"
        />
        <div>
          <label htmlFor="lock-preregistered" className="text-sm font-medium text-slate-800 cursor-pointer">
            Keep my current registration
          </label>
          <p className="text-xs text-slate-500">
            Preregistered courses will be included and locked in all generated schedules.
          </p>
        </div>
      </div>
    </div>
  );
}
