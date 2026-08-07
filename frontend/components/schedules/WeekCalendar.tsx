"use client";

import { RankedSchedule } from "@/lib/types";
import {
  CalendarBlock,
  CALENDAR_DAYS,
  getCalendarBlocks,
  getCalendarBounds,
  getSectionColor,
  cn,
} from "@/lib/schedule";
import { formatTime } from "@/lib/formatting";

const DAY_LABELS: Record<string, string> = {
  M: "Mon",
  T: "Tue",
  W: "Wed",
  R: "Thu",
  F: "Fri",
};

interface WeekCalendarProps {
  schedule: RankedSchedule;
}

function timeLabel(minutes: number): string {
  return formatTime(
    `${Math.floor(minutes / 60).toString().padStart(2, "0")}:${(minutes % 60).toString().padStart(2, "0")}`,
  );
}

export function WeekCalendar({ schedule }: WeekCalendarProps) {
  const blocks = getCalendarBlocks(schedule);
  const [startMin, endMin] = getCalendarBounds(blocks);
  const totalMinutes = endMin - startMin;
  const heightPx = Math.max(300, totalMinutes * 1.5);

  const hourMarkers: number[] = [];
  const firstHour = Math.ceil(startMin / 60);
  const lastHour = Math.floor(endMin / 60);
  for (let h = firstHour; h <= lastHour; h++) {
    hourMarkers.push(h * 60);
  }

  const toPercent = (min: number) => ((min - startMin) / totalMinutes) * 100;

  const blocksByDay = new Map<string, CalendarBlock[]>();
  for (const day of CALENDAR_DAYS) blocksByDay.set(day, []);
  for (const b of blocks) {
    blocksByDay.get(b.day)?.push(b);
  }

  // Build accessible text description of the schedule.
  const accessibleDescription = CALENDAR_DAYS.map((day) => {
    const dayBlocks = blocksByDay.get(day) ?? [];
    if (dayBlocks.length === 0) return `${DAY_LABELS[day]}: no classes`;
    const items = dayBlocks.map(
      (b) => `${b.section.course_code}${b.isLinked ? " (lab/drill)" : ""} ${formatTime(schedule.parent_sections.find((s) => s.course_code === b.section.course_code)?.meeting_times[0]?.start ?? b.section.meeting_times[0]?.start ?? "")}`
    );
    return `${DAY_LABELS[day]}: ${items.join(", ")}`;
  }).join(". ");

  return (
    <div>
      {/* Screen-reader accessible description */}
      <p className="sr-only">{accessibleDescription}</p>

      <div className="overflow-x-auto -mx-1">
        <div className="min-w-[420px]">
          {/* Day headers */}
          <div className="grid grid-cols-[3rem_1fr_1fr_1fr_1fr_1fr] border-b border-slate-200 pb-2 mb-1">
            <div /> {/* time label gutter */}
            {CALENDAR_DAYS.map((day) => (
              <div key={day} className="text-center text-xs font-medium text-slate-500">
                {DAY_LABELS[day]}
              </div>
            ))}
          </div>

          {/* Grid body */}
          <div
            className="grid grid-cols-[3rem_1fr_1fr_1fr_1fr_1fr] relative"
            style={{ height: `${heightPx}px` }}
            aria-hidden="true"
          >
            {/* Hour lines + labels */}
            {hourMarkers.map((min) => (
              <div
                key={min}
                className="absolute left-0 right-0 flex items-start"
                style={{ top: `${toPercent(min)}%` }}
              >
                <span className="w-12 shrink-0 pr-2 text-right text-[10px] text-slate-400 -translate-y-2">
                  {timeLabel(min)}
                </span>
                <div className="flex-1 border-t border-slate-100" />
              </div>
            ))}

            {/* Day columns with course blocks */}
            <div /> {/* gutter */}
            {CALENDAR_DAYS.map((day) => (
              <div key={day} className="relative border-l border-slate-100">
                {(blocksByDay.get(day) ?? []).map((block, i) => {
                  const top = toPercent(block.startMin);
                  const height = toPercent(block.endMin) - top;
                  const color = getSectionColor(block.section.course_code);
                  return (
                    <div
                      key={i}
                      className={cn(
                        "absolute left-0.5 right-0.5 overflow-hidden rounded-md border px-1 py-0.5",
                        color,
                        block.isLinked && "opacity-80",
                      )}
                      style={{ top: `${top}%`, height: `${height}%` }}
                      title={`${block.section.course_code}: ${block.section.title}${block.isLinked ? " (lab/drill)" : ""}`}
                    >
                      <p className="truncate text-[10px] font-semibold leading-tight">
                        {block.section.course_code}
                        {block.isLinked && (
                          <span className="ml-1 rounded bg-black/10 px-1 text-[9px] font-normal">
                            {block.section.course_type.toUpperCase().slice(0, 3)}
                          </span>
                        )}
                      </p>
                      {height > 8 && (
                        <p className="truncate text-[9px] leading-tight opacity-80">
                          {formatTime(block.section.meeting_times[0]?.start ?? "")}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
