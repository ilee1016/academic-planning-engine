// Display formatting utilities.
// All pure functions — no side effects, no imports from React.

// ---------------------------------------------------------------------------
// Category labels
// ---------------------------------------------------------------------------

const CATEGORY_LABELS: Record<string, string> = {
  requirements_first: "Best for Degree Progress",
  preferred_subjects: "Best for Your Subject Preferences",
  compact_schedule: "Most Compact Week",
  balanced: "Most Balanced",
  current_registration: "Your Current Registration",
};

export function formatCategoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category;
}

// ---------------------------------------------------------------------------
// Score component labels
// ---------------------------------------------------------------------------

const SCORE_LABELS: Record<string, string> = {
  requirement_gains: "Degree progress",
  preferred_subjects: "Subject preferences",
  free_days: "Free-day fit",
  compactness: "Schedule compactness",
  credit_load: "Credit target",
};

export function formatScoreLabel(key: string): string {
  return SCORE_LABELS[key] ?? key;
}

// ---------------------------------------------------------------------------
// Weekday formatting
// ---------------------------------------------------------------------------

const DAY_NAMES: Record<string, string> = {
  M: "Monday",
  T: "Tuesday",
  W: "Wednesday",
  R: "Thursday",
  F: "Friday",
};

const DAY_ABBR: Record<string, string> = {
  M: "Mon",
  T: "Tue",
  W: "Wed",
  R: "Thu",
  F: "Fri",
};

export function formatDayFull(day: string): string {
  return DAY_NAMES[day] ?? day;
}

export function formatDayAbbr(day: string): string {
  return DAY_ABBR[day] ?? day;
}

export function formatDayList(days: string[]): string {
  if (days.length === 0) return "";
  if (days.length === 1) return formatDayFull(days[0]);
  const last = days[days.length - 1];
  const rest = days.slice(0, -1).map(formatDayAbbr);
  return `${rest.join(", ")} & ${formatDayFull(last)}`;
}

// ---------------------------------------------------------------------------
// Time formatting
// ---------------------------------------------------------------------------

// "HH:MM" → "10:30 AM"
export function formatTime(hhmm: string): string {
  const [hStr, mStr] = hhmm.split(":");
  const h = parseInt(hStr, 10);
  const m = parseInt(mStr, 10);
  const period = h >= 12 ? "PM" : "AM";
  const h12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  const mm = m.toString().padStart(2, "0");
  return `${h12}:${mm} ${period}`;
}

export function formatTimeRange(start: string, end: string): string {
  return `${formatTime(start)}–${formatTime(end)}`;
}

// Returns total minutes since midnight for a "HH:MM" string.
export function timeToMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

// ---------------------------------------------------------------------------
// Credits
// ---------------------------------------------------------------------------

export function formatCredits(credits: string): string {
  const n = parseFloat(credits);
  if (isNaN(n)) return credits;
  return n % 1 === 0 ? n.toString() : n.toFixed(1);
}

// ---------------------------------------------------------------------------
// Requirement gain wording — must never say "completes"
// ---------------------------------------------------------------------------

export function requirementGainVerb(): string {
  return "Makes progress toward";
}

// ---------------------------------------------------------------------------
// Solver cap notice
// ---------------------------------------------------------------------------

export function solverCapNotice(): string {
  return (
    "The planner evaluated a capped set of valid combinations. These are the " +
    "highest-ranked schedules generated, not a guarantee of the single global optimum."
  );
}
