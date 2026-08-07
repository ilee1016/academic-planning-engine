"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createSession, uploadInputs } from "@/lib/api";
import { ApiError } from "@/lib/types";
import { FileUploadCard } from "@/components/upload/FileUploadCard";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";

const UPLOAD_MESSAGES = [
  "Reading Degree Works audit…",
  "Reading course catalog…",
  "Identifying remaining requirements…",
];

function mapErrorCode(code: string): string {
  switch (code) {
    case "audit_parse_failed":
      return "We couldn't recognize this as a Swarthmore Degree Works audit. Please check that you uploaded the correct PDF.";
    case "catalog_parse_failed":
      return "We couldn't read this course catalog. Please check that you uploaded a valid CSV file.";
    case "unsupported_program":
      return "This version currently supports the Swarthmore Computer Science major (catalog year 202304).";
    case "invalid_file_type":
      return "Please upload the correct file types: PDF for the audit, CSV for the catalog.";
    case "audit_too_large":
    case "catalog_too_large":
      return "One of the uploaded files is too large. Files must be under 10 MB.";
    default:
      return "Upload failed. Please check both files and try again.";
  }
}

export default function HomePage() {
  const router = useRouter();
  const [auditFile, setAuditFile] = useState<File | null>(null);
  const [catalogFile, setCatalogFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMsgIdx, setUploadMsgIdx] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = auditFile !== null && catalogFile !== null && !uploading;

  const handleSubmit = async () => {
    if (!auditFile || !catalogFile) return;
    setError(null);
    setUploading(true);
    setUploadMsgIdx(0);

    const interval = setInterval(() => {
      setUploadMsgIdx((i) => Math.min(i + 1, UPLOAD_MESSAGES.length - 1));
    }, 1200);

    try {
      const prevId = sessionStorage.getItem("planning_session_id");
      if (prevId) {
        try {
          await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/session/${prevId}`, {
            method: "DELETE",
          });
        } catch {
          // Ignore cleanup errors.
        }
      }

      const session = await createSession();
      const sessionId = session.session_id;
      // Only store session_id, not any student data.
      sessionStorage.setItem("planning_session_id", sessionId);

      const summary = await uploadInputs(sessionId, auditFile, catalogFile);
      sessionStorage.setItem("planning_input_summary", JSON.stringify(summary));

      router.push("/planner");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(mapErrorCode(err.code));
      } else {
        setError("An unexpected error occurred. Please try again.");
      }
    } finally {
      clearInterval(interval);
      setUploading(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl px-4 py-12 space-y-10">
      <div className="text-center space-y-3">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900">
          Plan your semester around what you actually need.
        </h1>
        <p className="text-slate-600 max-w-lg mx-auto leading-relaxed">
          Upload your Swarthmore Degree Works audit and semester course catalog.
          The planner analyzes your remaining requirements and generates schedules
          that fit both your academic progress and your preferences.
        </p>
      </div>

      <div className="space-y-4">
        <FileUploadCard
          id="audit-file"
          label="Degree Works audit"
          description="Export from DegreeWorks → Print/Export → PDF"
          accept=".pdf"
          acceptLabel="PDF"
          file={auditFile}
          onFileChange={setAuditFile}
        />
        <FileUploadCard
          id="catalog-file"
          label="Course catalog"
          description="Fall 2026 catalog CSV exported from the registrar"
          accept=".csv"
          acceptLabel="CSV"
          file={catalogFile}
          onFileChange={setCatalogFile}
        />
      </div>

      <p className="text-center text-xs text-slate-400">
        Files are processed for this planning session and are not stored permanently.
      </p>

      {error && (
        <ErrorState
          message={error}
          onRetry={() => setError(null)}
        />
      )}

      {uploading && <LoadingState message={UPLOAD_MESSAGES[uploadMsgIdx]} />}

      {!uploading && (
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="w-full rounded-xl bg-slate-900 px-6 py-3.5 text-sm font-semibold text-white transition-colors hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2 disabled:opacity-40 disabled:cursor-not-allowed"
          aria-disabled={!canSubmit}
        >
          Analyze academic progress
        </button>
      )}
    </div>
  );
}
