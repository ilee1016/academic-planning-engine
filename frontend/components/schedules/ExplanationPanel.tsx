"use client";

import { useState } from "react";
import { getScheduleExplanation } from "@/lib/api";
import { ExplanationResponse } from "@/lib/types";
import { Skeleton } from "@/components/shared/LoadingState";

interface ExplanationPanelProps {
  sessionId: string;
  scheduleId: string;
}

export function ExplanationPanel({ sessionId, scheduleId }: ExplanationPanelProps) {
  const [state, setState] = useState<
    | { phase: "idle" }
    | { phase: "loading" }
    | { phase: "loaded"; result: ExplanationResponse }
    | { phase: "error" }
  >({ phase: "idle" });

  const load = async () => {
    setState({ phase: "loading" });
    try {
      const result = await getScheduleExplanation(sessionId, scheduleId);
      setState({ phase: "loaded", result });
    } catch {
      setState({ phase: "error" });
    }
  };

  if (state.phase === "idle") {
    return (
      <button
        onClick={load}
        className="text-sm font-medium text-slate-600 underline hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-400 rounded"
      >
        Why this schedule?
      </button>
    );
  }

  if (state.phase === "loading") {
    return (
      <div className="space-y-2" aria-live="polite" aria-label="Loading explanation">
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-3 w-5/6" />
        <Skeleton className="h-3 w-4/6" />
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <p className="text-sm text-slate-400" aria-live="polite">
        Explanation unavailable.
      </p>
    );
  }

  const { result } = state;
  return (
    <div className="space-y-2" aria-live="polite">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Why it ranked highly
      </h4>
      <p className="text-sm text-slate-700 leading-relaxed">{result.explanation}</p>
      {result.source === "fallback" && (
        <p className="text-xs text-slate-400">Planner summary</p>
      )}
    </div>
  );
}
