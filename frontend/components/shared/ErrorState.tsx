"use client";

import { cn } from "@/lib/schedule";

interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
  onStartOver?: () => void;
  className?: string;
}

export function ErrorState({ title = "Something went wrong", message, onRetry, onStartOver, className }: ErrorStateProps) {
  return (
    <div
      className={cn("rounded-xl border border-red-200 bg-red-50 p-6", className)}
      role="alert"
      aria-live="assertive"
    >
      <h3 className="font-semibold text-red-900">{title}</h3>
      <p className="mt-1 text-sm text-red-700">{message}</p>
      {(onRetry || onStartOver) && (
        <div className="mt-4 flex gap-3">
          {onRetry && (
            <button
              onClick={onRetry}
              className="rounded-lg border border-red-300 bg-white px-4 py-2 text-sm font-medium text-red-800 hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-500"
            >
              Try again
            </button>
          )}
          {onStartOver && (
            <button
              onClick={onStartOver}
              className="rounded-lg border border-red-200 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-red-500"
            >
              Start over
            </button>
          )}
        </div>
      )}
    </div>
  );
}
