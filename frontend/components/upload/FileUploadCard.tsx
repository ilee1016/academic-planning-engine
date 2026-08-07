"use client";

import { useRef, useState } from "react";
import { cn } from "@/lib/schedule";

interface FileUploadCardProps {
  label: string;
  description: string;
  accept: string;
  acceptLabel: string;
  file: File | null;
  onFileChange: (file: File | null) => void;
  id: string;
}

export function FileUploadCard({
  label,
  description,
  accept,
  acceptLabel,
  file,
  onFileChange,
  id,
}: FileUploadCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const validateFile = (f: File): string | null => {
    const ext = f.name.split(".").pop()?.toLowerCase();
    const allowed = accept.split(",").map((a) => a.trim().replace(".", "").toLowerCase());
    if (!ext || !allowed.includes(ext)) {
      return `File must be a ${acceptLabel} file.`;
    }
    return null;
  };

  const handleFile = (f: File) => {
    const err = validateFile(f);
    if (err) {
      setError(err);
      onFileChange(null);
      return;
    }
    setError(null);
    onFileChange(f);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) handleFile(f);
    e.target.value = "";
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  };

  const handleRemove = () => {
    setError(null);
    onFileChange(null);
  };

  return (
    <div className="space-y-2">
      <div
        className={cn(
          "relative flex flex-col items-center gap-3 rounded-xl border-2 border-dashed p-6 text-center transition-colors",
          isDragging
            ? "border-slate-500 bg-slate-50"
            : file
              ? "border-emerald-400 bg-emerald-50"
              : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50",
          error && "border-red-300 bg-red-50",
        )}
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
      >
        <input
          ref={inputRef}
          id={id}
          type="file"
          accept={accept}
          className="sr-only"
          onChange={handleInputChange}
          aria-label={label}
        />

        {file ? (
          <div className="flex w-full items-center justify-between gap-3">
            <div className="min-w-0 flex-1 text-left">
              <p className="truncate text-sm font-medium text-emerald-800" title={file.name}>
                {file.name}
              </p>
              <p className="text-xs text-emerald-600">
                {(file.size / 1024).toFixed(0)} KB
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                onClick={() => inputRef.current?.click()}
                className="rounded-md border border-emerald-300 bg-white px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                type="button"
              >
                Replace
              </button>
              <button
                onClick={handleRemove}
                className="rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-500"
                type="button"
                aria-label={`Remove ${file.name}`}
              >
                Remove
              </button>
            </div>
          </div>
        ) : (
          <>
            <div
              className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 text-slate-400"
              aria-hidden="true"
            >
              <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
            </div>
            <div>
              <p className="text-sm font-medium text-slate-700">{label}</p>
              <p className="mt-0.5 text-xs text-slate-500">{description}</p>
              <p className="mt-1 text-xs text-slate-400">
                {acceptLabel} · drag & drop or{" "}
                <button
                  onClick={() => inputRef.current?.click()}
                  className="font-medium text-slate-600 underline hover:text-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-500 rounded"
                  type="button"
                >
                  browse
                </button>
              </p>
            </div>
          </>
        )}
      </div>
      {error && (
        <p className="text-xs text-red-600" role="alert">{error}</p>
      )}
    </div>
  );
}
