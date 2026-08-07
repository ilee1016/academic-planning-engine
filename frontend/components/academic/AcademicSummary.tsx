"use client";

import { InputSummaryResponse } from "@/lib/types";
import { formatCredits } from "@/lib/formatting";

interface AcademicSummaryProps {
  summary: InputSummaryResponse;
}

export function AcademicSummary({ summary }: AcademicSummaryProps) {
  const { student_summary: s, requirement_summary: r, warnings } = summary;
  const applied = parseFloat(s.credits_applied);
  const required = parseFloat(s.credits_required);
  const pct = required > 0 ? Math.min(100, (applied / required) * 100) : 0;

  return (
    <div className="space-y-6">
      {/* Student profile */}
      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-900">{s.major}</h2>
            <p className="text-sm text-slate-500">Class of {s.class_year}</p>
          </div>
          <span className="shrink-0 rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
            Catalog {s.catalog_year}
          </span>
        </div>

        {/* Credit progress */}
        <div>
          <div className="flex justify-between text-sm">
            <span className="font-medium text-slate-700">Degree credits</span>
            <span className="text-slate-600">
              {formatCredits(s.credits_applied)} / {formatCredits(s.credits_required)}
            </span>
          </div>
          <div
            className="mt-2 h-2 w-full overflow-hidden rounded-full bg-slate-100"
            role="progressbar"
            aria-valuenow={applied}
            aria-valuemin={0}
            aria-valuemax={required}
            aria-label={`${formatCredits(s.credits_applied)} of ${formatCredits(s.credits_required)} degree credits applied`}
          >
            <div
              className="h-full rounded-full bg-slate-700 transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>

        {/* Requirement stats */}
        <div className="flex gap-6 text-sm">
          <div>
            <span className="block text-2xl font-bold text-slate-900">{r.unsatisfied_items}</span>
            <span className="text-slate-500">remaining requirements</span>
          </div>
          <div>
            <span className="block text-2xl font-bold text-slate-900">
              {r.total_items - r.unsatisfied_items}
            </span>
            <span className="text-slate-500">satisfied</span>
          </div>
          <div>
            <span className="block text-2xl font-bold text-slate-900">{r.total_items}</span>
            <span className="text-slate-500">total requirements</span>
          </div>
        </div>
      </div>

      {/* Warnings */}
      {warnings.length > 0 && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
          <h3 className="text-sm font-semibold text-amber-800">Notes</h3>
          <ul className="mt-2 space-y-1">
            {warnings.map((w, i) => (
              <li key={i} className="text-xs text-amber-700">
                {w}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
