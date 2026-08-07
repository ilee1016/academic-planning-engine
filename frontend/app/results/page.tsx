"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ScheduleResponse,
  ScheduleResultResponse,
  DiagnosticResultResponse,
  RankedSchedule,
} from "@/lib/types";
import { formatCategoryLabel, formatCredits, formatDayAbbr, formatTimeRange, requirementGainVerb, solverCapNotice } from "@/lib/formatting";
import { getFreeDays, getScheduleTimespan } from "@/lib/schedule";
import { ScheduleCard } from "@/components/schedules/ScheduleCard";
import { ErrorState } from "@/components/shared/ErrorState";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingState } from "@/components/shared/LoadingState";

const MAX_COMPARE = 3;

function DiagnosticView({
  result,
  onAdjust,
}: {
  result: DiagnosticResultResponse;
  onAdjust: () => void;
}) {
  const { diagnostic } = result;
  return (
    <div className="max-w-lg mx-auto space-y-6 py-8">
      <div className="rounded-xl border border-slate-200 bg-white p-6 space-y-4">
        <h2 className="font-semibold text-slate-900">We couldn&apos;t find a schedule with all of these constraints.</h2>

        {diagnostic.reasons.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Why</h3>
            <ul className="space-y-1">
              {diagnostic.reasons.map((r, i) => (
                <li key={i} className="text-sm text-slate-700">{r}</li>
              ))}
            </ul>
          </div>
        )}

        {diagnostic.suggested_relaxations.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Things to try
            </h3>
            <ul className="space-y-1">
              {diagnostic.suggested_relaxations.map((r, i) => (
                <li key={i} className="text-sm text-slate-700 flex gap-2">
                  <span className="text-slate-400">·</span>
                  {r}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <button
        onClick={onAdjust}
        className="w-full rounded-xl bg-slate-900 px-6 py-3.5 text-sm font-semibold text-white hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-500"
      >
        Adjust preferences
      </button>
    </div>
  );
}

function CompareView({
  schedules,
}: {
  schedules: RankedSchedule[];
}) {
  const heads = ["Attribute", ...schedules.map((_, i) => `Schedule ${String.fromCharCode(65 + i)}`)];

  const rows = [
    {
      label: "Credits",
      values: schedules.map((s) => formatCredits(s.total_credits)),
    },
    {
      label: "Category",
      values: schedules.map((s) => formatCategoryLabel(s.category)),
    },
    {
      label: "Free days",
      values: schedules.map((s) => {
        const days = getFreeDays(s);
        return days.length > 0 ? days.map(formatDayAbbr).join(", ") : "None";
      }),
    },
    {
      label: "Earliest class",
      values: schedules.map((s) => {
        const { earliest } = getScheduleTimespan(s);
        return earliest ? formatTimeRange(earliest, earliest).split("–")[0] : "—";
      }),
    },
    {
      label: "Latest class ends",
      values: schedules.map((s) => {
        const { latest } = getScheduleTimespan(s);
        return latest ? formatTimeRange(latest, latest).split("–")[0] : "—";
      }),
    },
    {
      label: requirementGainVerb(),
      values: schedules.map((s) =>
        s.requirement_gains.length > 0
          ? s.requirement_gains.map((g) => g.label).join(", ")
          : "None",
      ),
    },
    {
      label: "Courses",
      values: schedules.map((s) =>
        s.parent_sections.map((p) => p.course_code).join(", "),
      ),
    },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse" aria-label="Schedule comparison">
        <thead>
          <tr>
            {heads.map((h, i) => (
              <th
                key={i}
                scope={i === 0 ? "col" : "col"}
                className={`px-4 py-2 text-left font-semibold border-b border-slate-200 ${i === 0 ? "text-slate-500 w-36" : "text-slate-800"}`}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} className="border-b border-slate-100 hover:bg-slate-50">
              <th scope="row" className="px-4 py-2 text-left font-medium text-slate-500 text-xs">
                {row.label}
              </th>
              {row.values.map((v, i) => (
                <td key={i} className="px-4 py-2 text-slate-700">{v}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ResultsPage() {
  const router = useRouter();
  const [result] = useState<ScheduleResponse | null>(() => {
    if (typeof window === "undefined") return null;
    const raw = sessionStorage.getItem("planning_results");
    if (!raw) return null;
    try {
      return JSON.parse(raw) as ScheduleResponse;
    } catch {
      return null;
    }
  });
  const [sessionId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return sessionStorage.getItem("planning_session_id_for_results");
  });
  const [compareIds, setCompareIds] = useState<Set<string>>(new Set());
  const [showCompare, setShowCompare] = useState(false);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!result || !sessionId) {
      router.replace("/planner");
    }
  }, [result, sessionId, router]);

  const toggleCompare = (id: string) => {
    setCompareIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < MAX_COMPARE) {
        next.add(id);
      }
      return next;
    });
  };

  const handleAdjust = () => router.push("/planner");
  const handleStartOver = () => {
    sessionStorage.clear();
    router.push("/");
  };

  if (!result || !sessionId) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-12">
        <LoadingState message="Loading your schedules…" />
      </div>
    );
  }

  if (result.status === "no_valid_schedules") {
    return (
      <div className="mx-auto max-w-5xl px-4 py-8">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-bold text-slate-900">Your semester options</h1>
          <button onClick={handleStartOver} className="text-xs text-slate-400 hover:text-slate-600 underline">
            Start over
          </button>
        </div>
        <DiagnosticView result={result as DiagnosticResultResponse} onAdjust={handleAdjust} />
      </div>
    );
  }

  const found = result as ScheduleResultResponse;
  const { top_schedules, categories, search_metadata } = found;
  const topToShow = showAll ? top_schedules : top_schedules.slice(0, 3);

  // Get all category schedules that aren't already in top_schedules.
  const topIds = new Set(top_schedules.map((s) => s.schedule_id));
  const categoryEntries = Object.entries(categories).filter(([, schedules]) =>
    schedules.some((s) => !topIds.has(s.schedule_id)),
  );

  // Schedules selected for comparison.
  const compareSchedules = [...top_schedules, ...Object.values(categories).flat()].filter((s) =>
    compareIds.has(s.schedule_id),
  );

  if (top_schedules.length === 0) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-8">
        <EmptyState
          title="No schedules generated"
          description="Try adjusting your preferences."
          action={
            <button
              onClick={handleAdjust}
              className="rounded-xl bg-slate-900 px-6 py-3 text-sm font-semibold text-white hover:bg-slate-700"
            >
              Adjust preferences
            </button>
          }
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 space-y-8">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-bold text-slate-900">Your semester options</h1>
          <p className="mt-1 text-sm text-slate-500">
            These are the highest-ranked schedules generated from your academic requirements and preferences.
          </p>
        </div>
        <div className="flex gap-3 shrink-0">
          {compareIds.size >= 2 && (
            <button
              onClick={() => setShowCompare((x) => !x)}
              className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-400"
            >
              {showCompare ? "Hide comparison" : `Compare ${compareIds.size}`}
            </button>
          )}
          <button
            onClick={handleAdjust}
            className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-400"
          >
            Adjust preferences
          </button>
          <button
            onClick={handleStartOver}
            className="text-xs text-slate-400 hover:text-slate-600 underline self-center"
          >
            Start over
          </button>
        </div>
      </div>

      {/* Solver cap notice */}
      {!search_metadata.search_space_fully_enumerated && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          {solverCapNotice()}
        </div>
      )}

      {/* Current registration notice */}
      {top_schedules.some((s) => s.category === "current_registration") && (
        <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          Your registered courses already account for the selected maximum credit load,
          so the planner evaluated your existing schedule rather than adding courses.
        </div>
      )}

      {/* Comparison table */}
      {showCompare && compareSchedules.length >= 2 && (
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <h2 className="font-semibold text-slate-900 mb-4">Schedule comparison</h2>
          <CompareView schedules={compareSchedules} />
        </div>
      )}

      {/* Top schedules */}
      <section aria-label="Top schedules">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 mb-4">
          Top schedules
          {compareIds.size > 0 && (
            <span className="ml-2 font-normal normal-case text-slate-400">
              ({compareIds.size}/{MAX_COMPARE} selected for comparison)
            </span>
          )}
        </h2>
        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
          {topToShow.map((s, i) => (
            <ScheduleCard
              key={s.schedule_id}
              schedule={s}
              sessionId={sessionId}
              rank={i + 1}
              isComparing={compareIds.has(s.schedule_id)}
              onToggleCompare={toggleCompare}
              compareDisabled={compareIds.size >= MAX_COMPARE}
            />
          ))}
        </div>
        {top_schedules.length > 3 && !showAll && (
          <button
            onClick={() => setShowAll(true)}
            className="mt-4 w-full rounded-xl border border-slate-200 bg-white py-3 text-sm font-medium text-slate-600 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-400"
          >
            Show {top_schedules.length - 3} more top schedules
          </button>
        )}
      </section>

      {/* Category sections */}
      {categoryEntries.map(([category, schedules]) => {
        const extraSchedules = schedules.filter((s) => !topIds.has(s.schedule_id));
        if (extraSchedules.length === 0) return null;
        return (
          <section key={category} aria-label={formatCategoryLabel(category)}>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500 mb-4">
              {formatCategoryLabel(category)}
            </h2>
            <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              {extraSchedules.map((s) => (
                <ScheduleCard
                  key={s.schedule_id}
                  schedule={s}
                  sessionId={sessionId}
                  isComparing={compareIds.has(s.schedule_id)}
                  onToggleCompare={toggleCompare}
                  compareDisabled={compareIds.size >= MAX_COMPARE}
                />
              ))}
            </div>
          </section>
        );
      })}

      {/* Search metadata */}
      <details className="text-xs text-slate-400">
        <summary className="cursor-pointer hover:text-slate-600 focus:outline-none">
          Search details
        </summary>
        <div className="mt-2 space-y-0.5 pl-2">
          <p>Candidates evaluated: {search_metadata.candidate_count}</p>
          <p>Selection options: {search_metadata.option_count}</p>
          <p>Schedules generated: {search_metadata.generated_schedules}</p>
          <p>Result cap: {search_metadata.solver_cap}</p>
          <p>
            Fully enumerated:{" "}
            {search_metadata.search_space_fully_enumerated ? "Yes" : "No (cap reached)"}
          </p>
        </div>
      </details>

      {/* Error fallback */}
      {top_schedules.length === 0 && (
        <ErrorState
          message="No schedules could be displayed."
          onRetry={handleAdjust}
        />
      )}
    </div>
  );
}
