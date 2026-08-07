"use client";

import { cn } from "@/lib/schedule";

interface LoadingStateProps {
  message?: string;
  className?: string;
}

export function LoadingState({ message = "Loading…", className }: LoadingStateProps) {
  return (
    <div
      className={cn("flex flex-col items-center gap-4 py-12", className)}
      role="status"
      aria-label={message}
      aria-live="polite"
    >
      <div
        className="h-10 w-10 animate-spin rounded-full border-4 border-slate-200 border-t-slate-700"
        aria-hidden="true"
      />
      <p className="text-sm text-slate-500">{message}</p>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded-lg bg-slate-100", className)}
      aria-hidden="true"
    />
  );
}
