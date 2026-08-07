"use client";

import { useState } from "react";
import { RankedSchedule } from "@/lib/types";
import {
  formatCategoryLabel,
  formatCredits,
  formatScoreLabel,
  formatTimeRange,
  formatDayAbbr,
  requirementGainVerb,
} from "@/lib/formatting";
import { getFreeDays, getScheduleTimespan } from "@/lib/schedule";
import { WeekCalendar } from "./WeekCalendar";
import { ExplanationPanel } from "./ExplanationPanel";

interface ScheduleCardProps {
  schedule: RankedSchedule;
  sessionId: string;
  rank?: number;
  isComparing?: boolean;
  onToggleCompare?: (id: string) => void;
  compareDisabled?: boolean;
}

export function ScheduleCard({
  schedule,
  sessionId,
  rank,
  isComparing = false,
  onToggleCompare,
  compareDisabled = false,
}: ScheduleCardProps) {
  const [expanded, setExpanded] = useState(false);
  const freeDays = getFreeDays(schedule);
  const { earliest, latest } = getScheduleTimespan(schedule);

  const scoreComponents = Object.entries(schedule.score_breakdown)
    .filter(([, v]) => v > 0)
    .sort(([, a], [, b]) => b - a);

  return (
    <article
      className="rounded-xl border border-slate-200 bg-white overflow-hidden"
      aria-label={`Schedule: ${formatCategoryLabel(schedule.category)}`}
    >
      {/* Header */}
      <div className="border-b border-slate-100 bg-slate-50 px-5 py-4 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {rank !== undefined && (
              <span className="shrink-0 rounded-full bg-slate-700 px-2 py-0.5 text-xs font-semibold text-white">
                #{rank}
              </span>
            )}
            <span className="text-sm font-semibold text-slate-800">
              {formatCategoryLabel(schedule.category)}
            </span>
          </div>
          <p className="mt-1 text-2xl font-bold text-slate-900">
            {formatCredits(schedule.total_credits)}{" "}
            <span className="text-sm font-normal text-slate-500">credits</span>
          </p>
        </div>
        {onToggleCompare && (
          <button
            type="button"
            onClick={() => onToggleCompare(schedule.schedule_id)}
            disabled={compareDisabled && !isComparing}
            aria-pressed={isComparing}
            className="shrink-0 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-400 disabled:opacity-40"
          >
            {isComparing ? "Remove" : "Compare"}
          </button>
        )}
      </div>

      <div className="px-5 py-4 space-y-5">
        {/* Courses */}
        <section aria-label="Courses">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
            Courses
          </h3>
          <ul className="space-y-2">
            {schedule.parent_sections.map((s) => {
              const lab = schedule.linked_sections.find(
                (l) => l.course_code === s.course_code,
              );
              return (
                <li key={s.ref_no} className="space-y-0.5">
                  <div className="flex items-baseline gap-2">
                    <span className="font-medium text-slate-800 text-sm">{s.course_code}</span>
                    <span className="text-sm text-slate-600 truncate">{s.title}</span>
                    <span className="shrink-0 text-xs text-slate-400">
                      {formatCredits(s.credits)} cr
                    </span>
                  </div>
                  {s.meeting_times.length > 0 && (
                    <p className="text-xs text-slate-500 ml-0">
                      {s.meeting_times
                        .map((mt) => `${mt.days.map(formatDayAbbr).join("")} ${formatTimeRange(mt.start, mt.end)}`)
                        .join(" · ")}
                    </p>
                  )}
                  {lab && (
                    <p className="text-xs text-slate-400 ml-2">
                      + {lab.course_type}:{" "}
                      {lab.meeting_times
                        .map((mt) => `${mt.days.map(formatDayAbbr).join("")} ${formatTimeRange(mt.start, mt.end)}`)
                        .join(" · ")}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </section>

        {/* Requirement gains */}
        {schedule.requirement_gains.length > 0 && (
          <section aria-label="Requirement progress">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              {requirementGainVerb()}
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {schedule.requirement_gains.map((g) => (
                <span
                  key={g.id}
                  className="rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-800"
                  title={g.notes || undefined}
                >
                  {g.label}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Key strengths */}
        {scoreComponents.length > 0 && (
          <section aria-label="Key strengths">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Strengths
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {scoreComponents.slice(0, 3).map(([key]) => (
                <span
                  key={key}
                  className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600"
                >
                  {formatScoreLabel(key)}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Quick info: free days, time window */}
        <div className="flex flex-wrap gap-4 text-xs text-slate-500">
          {freeDays.length > 0 && (
            <span>
              <strong className="text-slate-700">Free: </strong>
              {freeDays.map(formatDayAbbr).join(", ")}
            </span>
          )}
          {earliest && latest && (
            <span>
              <strong className="text-slate-700">Hours: </strong>
              {formatTimeRange(earliest, latest)}
            </span>
          )}
        </div>

        {/* Weekly calendar toggle */}
        <div>
          <button
            type="button"
            onClick={() => setExpanded((x) => !x)}
            className="flex items-center gap-1.5 text-sm font-medium text-slate-600 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-400 rounded"
            aria-expanded={expanded}
            aria-controls={`calendar-${schedule.schedule_id}`}
          >
            <svg
              className={`h-4 w-4 transition-transform ${expanded ? "rotate-90" : ""}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            {expanded ? "Hide" : "Show"} weekly calendar
          </button>

          {expanded && (
            <div
              id={`calendar-${schedule.schedule_id}`}
              className="mt-3 border-t border-slate-100 pt-3"
            >
              <WeekCalendar schedule={schedule} />
            </div>
          )}
        </div>

        {/* Explanation */}
        <div className="border-t border-slate-100 pt-4">
          <ExplanationPanel sessionId={sessionId} scheduleId={schedule.schedule_id} />
        </div>

        {/* Score detail (expandable) */}
        <details className="text-xs">
          <summary className="cursor-pointer text-slate-400 hover:text-slate-600 focus:outline-none focus:ring-1 focus:ring-slate-400 rounded">
            Planner score: {schedule.score.toFixed(0)}
          </summary>
          <div className="mt-2 space-y-1 pl-2">
            {Object.entries(schedule.score_breakdown).map(([key, val]) => (
              <div key={key} className="flex justify-between text-slate-500">
                <span>{formatScoreLabel(key)}</span>
                <span>{val.toFixed(0)}</span>
              </div>
            ))}
          </div>
        </details>
      </div>
    </article>
  );
}
