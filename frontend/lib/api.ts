// All HTTP calls go through this module.
// Components must not call fetch() directly.

import {
  AmbiguousCourse,
  AmbiguityError,
  ApiError,
  ApiErrorEnvelope,
  CreateSessionResponse,
  ExplanationResponse,
  GenerateSchedulesRequest,
  InputSummaryResponse,
  ScheduleResponse,
  SessionInfoResponse,
} from "./types";

function getBaseUrl(): string {
  const url = process.env.NEXT_PUBLIC_API_URL;
  if (!url) {
    throw new Error("NEXT_PUBLIC_API_URL is not set");
  }
  return url.replace(/\/$/, "");
}

async function parseError(res: Response): Promise<ApiError> {
  let envelope: ApiErrorEnvelope | null = null;
  try {
    envelope = (await res.json()) as ApiErrorEnvelope;
  } catch {
    // Not JSON — fall through to generic error.
  }

  const detail = envelope?.error;
  if (detail) {
    // Surface ambiguity as a typed error.
    if (detail.code === "locked_section_selection_required") {
      const courses = (detail.details?.courses as AmbiguousCourse[] | undefined) ?? [];
      return new AmbiguityError(courses);
    }
    return new ApiError(detail.code, detail.message, res.status, detail.details ?? {});
  }

  // FastAPI validation errors (422) come as { detail: [...] } not our envelope.
  return new ApiError("http_error", `HTTP ${res.status}`, res.status);
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      Accept: "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    throw await parseError(res);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------

export async function createSession(): Promise<CreateSessionResponse> {
  return request<CreateSessionResponse>(`${getBaseUrl()}/api/session`, {
    method: "POST",
  });
}

export async function getSession(sessionId: string): Promise<SessionInfoResponse> {
  return request<SessionInfoResponse>(`${getBaseUrl()}/api/session/${sessionId}`);
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${getBaseUrl()}/api/session/${sessionId}`, {
    method: "DELETE",
  });
  // 204 → success, 404 → already gone, both are fine.
  if (!res.ok && res.status !== 404) {
    throw await parseError(res);
  }
}

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------

export async function uploadInputs(
  sessionId: string,
  auditFile: File,
  catalogFile: File,
): Promise<InputSummaryResponse> {
  const form = new FormData();
  form.append("audit_file", auditFile);
  form.append("catalog_file", catalogFile);

  const res = await fetch(`${getBaseUrl()}/api/session/${sessionId}/inputs`, {
    method: "POST",
    body: form,
    // No Content-Type header — browser sets multipart boundary automatically.
  });
  if (!res.ok) {
    throw await parseError(res);
  }
  return res.json() as Promise<InputSummaryResponse>;
}

// ---------------------------------------------------------------------------
// Schedule generation
// ---------------------------------------------------------------------------

export async function generateSchedules(
  sessionId: string,
  req: GenerateSchedulesRequest,
): Promise<ScheduleResponse> {
  return request<ScheduleResponse>(
    `${getBaseUrl()}/api/session/${sessionId}/schedules`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    },
  );
}

// ---------------------------------------------------------------------------
// Explanation
// ---------------------------------------------------------------------------

export async function getScheduleExplanation(
  sessionId: string,
  scheduleId: string,
): Promise<ExplanationResponse> {
  return request<ExplanationResponse>(
    `${getBaseUrl()}/api/session/${sessionId}/schedules/${scheduleId}/explanation`,
    { method: "POST" },
  );
}
