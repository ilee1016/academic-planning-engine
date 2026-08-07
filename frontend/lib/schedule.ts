// Schedule display helpers and cn utility.

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { MeetingTime, RankedSchedule, Section } from "./types";
import { timeToMinutes } from "./formatting";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

// ---------------------------------------------------------------------------
// Calendar layout helpers
// ---------------------------------------------------------------------------

export const CALENDAR_DAYS = ["M", "T", "W", "R", "F"] as const;
export type CalendarDay = (typeof CALENDAR_DAYS)[number];

// Returns all meeting-time blocks for a schedule, tagged with section info.
export interface CalendarBlock {
  section: Section;
  day: string;
  startMin: number;
  endMin: number;
  isLinked: boolean;
}

export function getCalendarBlocks(schedule: RankedSchedule): CalendarBlock[] {
  const blocks: CalendarBlock[] = [];

  const addBlocks = (section: Section, isLinked: boolean) => {
    for (const mt of section.meeting_times) {
      for (const day of mt.days) {
        blocks.push({
          section,
          day,
          startMin: timeToMinutes(mt.start),
          endMin: timeToMinutes(mt.end),
          isLinked,
        });
      }
    }
  };

  for (const s of schedule.parent_sections) addBlocks(s, false);
  for (const s of schedule.linked_sections) addBlocks(s, true);

  return blocks;
}

// Returns [min_start, max_end] in minutes across all blocks.
export function getCalendarBounds(blocks: CalendarBlock[]): [number, number] {
  if (blocks.length === 0) return [8 * 60, 18 * 60];
  const starts = blocks.map((b) => b.startMin);
  const ends = blocks.map((b) => b.endMin);
  const minStart = Math.min(...starts);
  const maxEnd = Math.max(...ends);
  // Round to nearest hour, with padding.
  const paddedStart = Math.max(0, Math.floor(minStart / 60) * 60 - 30);
  const paddedEnd = Math.min(24 * 60, Math.ceil(maxEnd / 60) * 60 + 30);
  return [paddedStart, paddedEnd];
}

// Get a deterministic color class for a section based on its course code.
const COLORS = [
  "bg-blue-100 border-blue-400 text-blue-900",
  "bg-emerald-100 border-emerald-400 text-emerald-900",
  "bg-violet-100 border-violet-400 text-violet-900",
  "bg-amber-100 border-amber-400 text-amber-900",
  "bg-rose-100 border-rose-400 text-rose-900",
  "bg-cyan-100 border-cyan-400 text-cyan-900",
];

export function getSectionColor(courseCode: string): string {
  let hash = 0;
  for (let i = 0; i < courseCode.length; i++) {
    hash = (hash * 31 + courseCode.charCodeAt(i)) & 0xffffffff;
  }
  return COLORS[Math.abs(hash) % COLORS.length];
}

// Group sections by their course_code for display.
export function groupLinkedSections(
  parentSections: Section[],
  linkedSections: Section[],
): Map<string, { parent: Section; linked: Section[] }> {
  const map = new Map<string, { parent: Section; linked: Section[] }>();

  for (const s of parentSections) {
    map.set(s.course_code, { parent: s, linked: [] });
  }
  for (const s of linkedSections) {
    const entry = map.get(s.course_code);
    if (entry) {
      entry.linked.push(s);
    }
  }
  return map;
}

// Return all unique free days for a schedule (days with no meeting times).
export function getFreeDays(schedule: RankedSchedule): string[] {
  const busyDays = new Set<string>();
  const allSections = [...schedule.parent_sections, ...schedule.linked_sections];
  for (const s of allSections) {
    for (const mt of s.meeting_times) {
      for (const d of mt.days) busyDays.add(d);
    }
  }
  return CALENDAR_DAYS.filter((d) => !busyDays.has(d));
}

// Earliest start and latest end across all sections.
export function getScheduleTimespan(schedule: RankedSchedule): {
  earliest: string | null;
  latest: string | null;
} {
  const allMt: MeetingTime[] = [
    ...schedule.parent_sections.flatMap((s) => s.meeting_times),
    ...schedule.linked_sections.flatMap((s) => s.meeting_times),
  ];
  if (allMt.length === 0) return { earliest: null, latest: null };

  const starts = allMt.map((m) => timeToMinutes(m.start));
  const ends = allMt.map((m) => timeToMinutes(m.end));
  const minStart = Math.min(...starts);
  const maxEnd = Math.max(...ends);

  const toHhmm = (mins: number) =>
    `${Math.floor(mins / 60).toString().padStart(2, "0")}:${(mins % 60).toString().padStart(2, "0")}`;

  return { earliest: toHhmm(minStart), latest: toHhmm(maxEnd) };
}
