"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { generateSchedules } from "@/lib/api";
import {
  AmbiguityError,
  ApiError,
  AmbiguousCourse,
  InputSummaryResponse,
  ScheduleResponse,
} from "@/lib/types";
import { AcademicSummary } from "@/components/academic/AcademicSummary";
import {
  PreferencesForm,
  PreferencesFormValue,
  defaultPreferences,
  toPreferencesRequest,
} from "@/components/planner/PreferencesForm";
import { LockedSectionResolver } from "@/components/planner/LockedSectionResolver";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";

const GENERATE_MESSAGES = [
  "Checking course combinations…",
  "Applying your constraints…",
  "Ranking generated schedules…",
];

function mapScheduleError(err: ApiError): string {
  switch (err.code) {
    case "inputs_not_uploaded":
      return "Session data is missing. Please start over and upload your files.";
    case "unknown_locked_ref":
      return "One or more selected sections could not be found. Please try again.";
    case "invalid_locked_schedule":
      return "The selected sections have a time conflict or credit-limit violation. Please adjust and try again.";
    default:
      return "Schedule generation failed. Please adjust your preferences and try again.";
  }
}

export default function PlannerPage() {
  const router = useRouter();
  const [summary] = useState<InputSummaryResponse | null>(() => {
    if (typeof window === "undefined") return null;
    const raw = sessionStorage.getItem("planning_input_summary");
    if (!raw) return null;
    try {
      return JSON.parse(raw) as InputSummaryResponse;
    } catch {
      return null;
    }
  });
  const [prefs, setPrefs] = useState<PreferencesFormValue>(defaultPreferences());
  const [generating, setGenerating] = useState(false);
  const [msgIdx, setMsgIdx] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [ambiguous, setAmbiguous] = useState<AmbiguousCourse[] | null>(null);
  const [selections, setSelections] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!summary) {
      router.replace("/");
    }
  }, [summary, router]);

  const getSessionId = (): string | null => sessionStorage.getItem("planning_session_id");

  const handleGenerate = async (lockedRefs: string[] = []) => {
    const sessionId = getSessionId();
    if (!sessionId || !summary) return;

    setError(null);
    setAmbiguous(null);
    setGenerating(true);
    setMsgIdx(0);

    const interval = setInterval(() => {
      setMsgIdx((i) => Math.min(i + 1, GENERATE_MESSAGES.length - 1));
    }, 1500);

    try {
      const result: ScheduleResponse = await generateSchedules(sessionId, {
        preferences: toPreferencesRequest(prefs),
        locked_ref_nos: lockedRefs,
      });
      sessionStorage.setItem("planning_results", JSON.stringify(result));
      sessionStorage.setItem("planning_session_id_for_results", sessionId);
      router.push("/results");
    } catch (err) {
      if (err instanceof AmbiguityError) {
        setAmbiguous(err.courses);
        setSelections({});
      } else if (err instanceof ApiError) {
        setError(mapScheduleError(err));
      } else {
        setError("An unexpected error occurred. Please try again.");
      }
    } finally {
      clearInterval(interval);
      setGenerating(false);
    }
  };

  const handleAmbiguitySubmit = () => {
    if (!ambiguous) return;
    const allSelected = ambiguous.every((c) => selections[c.course_code]);
    if (!allSelected) return;
    const refs = Object.values(selections);
    handleGenerate(refs);
  };

  const handleStartOver = () => {
    sessionStorage.clear();
    router.push("/");
  };

  if (!summary) {
    return <LoadingState message="Loading your academic profile…" />;
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 space-y-8">
      <div className="grid gap-8 lg:grid-cols-[1fr_1.4fr]">
        {/* Left: academic summary */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h1 className="text-xl font-bold text-slate-900">Your academic profile</h1>
            <button
              onClick={handleStartOver}
              className="text-xs text-slate-400 hover:text-slate-600 underline focus:outline-none focus:ring-2 focus:ring-slate-400 rounded"
            >
              Start over
            </button>
          </div>
          <AcademicSummary summary={summary} />
        </div>

        {/* Right: preferences */}
        <div className="space-y-4">
          <h2 className="text-xl font-bold text-slate-900">Your preferences</h2>

          {ambiguous ? (
            <div className="space-y-4">
              <LockedSectionResolver
                courses={ambiguous}
                selections={selections}
                onSelect={(code, ref) =>
                  setSelections((s) => ({ ...s, [code]: ref }))
                }
              />
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={handleAmbiguitySubmit}
                  disabled={!ambiguous.every((c) => selections[c.course_code]) || generating}
                  className="flex-1 rounded-xl bg-slate-900 px-6 py-3 text-sm font-semibold text-white hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-500 disabled:opacity-40"
                >
                  Generate schedules
                </button>
                <button
                  type="button"
                  onClick={() => setAmbiguous(null)}
                  className="rounded-xl border border-slate-200 px-4 py-3 text-sm text-slate-600 hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-slate-400"
                >
                  Back
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              <PreferencesForm
                value={prefs}
                onChange={setPrefs}
                disabled={generating}
              />

              {error && (
                <ErrorState
                  message={error}
                  onRetry={() => setError(null)}
                />
              )}

              {generating ? (
                <LoadingState message={GENERATE_MESSAGES[msgIdx]} />
              ) : (
                <button
                  type="button"
                  onClick={() => handleGenerate()}
                  className="w-full rounded-xl bg-slate-900 px-6 py-3.5 text-sm font-semibold text-white hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-500 disabled:opacity-40"
                >
                  Generate schedules
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
